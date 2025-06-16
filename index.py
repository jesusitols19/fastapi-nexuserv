from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from azure.storage.blob import BlobServiceClient
import pyodbc
import httpx
import fitz  # PyMuPDF
from uuid import uuid4
from dotenv import load_dotenv
from datetime import datetime
load_dotenv()
import os
import pymssql
from openai import OpenAI  # ✅ NUEVA LIBRERÍA

# Usa la clave de OpenAI desde variable de entorno
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

blob_service_client = BlobServiceClient.from_connection_string(os.getenv("AZURE_STORAGE_CONNECTION_STRING"))
CONTAINER_NAME = "postulaciones"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "https://lemon-bush-042e64010.6.azurestaticapps.net"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def extraer_texto_pdf(path: str) -> str:
    doc = fitz.open(path)
    texto = ""
    for pagina in doc:
        texto += pagina.get_text()
    return texto


async def analizar_con_gpt4o(texto_cv: str) -> str:
    prompt = f"""
Este es el contenido de un currículum vitae de una persona que quiere postular a nuestra empresa:

✅ CRITERIOS PARA SER APTO:
- Profesión: Licenciada en Arquitectura
- Experiencia: Al menos 5 años en proyectos residenciales y espacios públicos
- Experiencia adicional: Asistente de proyecto en Urbanlab 3 años
- Habilidades: Diseño arquitectónico y urbanismo, SketchUp, normativas, comunicación con clientes
- Idioma: Inglés básico

Analiza el siguiente CV textual y responde si corresponde exactamente a este perfil. Al no tener estas caracteristicas, marcalo como ❌ No apto.

Resume brevemente los motivos (3-5 líneas). Al final responde SOLO con:

✅ Apto  
❌ No apto

CV:
{texto_cv}
"""
    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return respuesta.choices[0].message.content


@app.get("/")
def root():
    return {"message": "¡Hola desde Azure!"}


@app.post("/postulaciones")
async def crear_postulacion(
    usuario: str = Form(...),
    fecha_nacimiento: str = Form(...),
    nombres: str = Form(...),
    apellidos: str = Form(...),
    correo: str = Form(...),
    celular: str = Form(...),
    dni: str = Form(...),
    cv: UploadFile = File(...)
):
    ruta = f"uploads/{cv.filename}"

    with open(ruta, "wb") as f:
        f.write(await cv.read())

    texto = extraer_texto_pdf(ruta)

    # Simulación del resultado de IA
    try:
        resultado = await analizar_con_gpt4o(texto)
    except Exception as e:
        os.remove(ruta)
        resultado = f"❌ Error al procesar el CV: {str(e)}"

    # Determinar estado
    estado = "Apto" if resultado.strip().endswith("✅ Apto") else "No Apto"

    # Subir a Azure Blob Storage
    blob_name = f"{uuid4()}_{cv.filename}"
    blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
    with open(ruta, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)

    os.remove(ruta)  # limpiar

    # Insertar en base de datos usando pymssql
    try:
        conn = pymssql.connect(
            server=os.getenv("DB_SERVER"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO postulaciones (usuario, nombres, apellidos, correo, celular, dni, fecha_nacimiento, cv_ruta, resultado_ia, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            usuario,
            nombres,
            apellidos,
            correo,
            celular,
            dni,
            datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date(),
            blob_name,
            resultado,
            estado
        ))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as db_error:
        print(f"[ERROR SQL] {db_error}")
        return {"error": f"No se pudo guardar en la base de datos: {str(db_error)}"}

    return {
        "usuario": usuario,
        "estado": estado,
        "ruta_en_blob": blob_name,
        "resultado_ia": resultado
    }

@app.get("/test-db")
def test_db():
    try:
        conn = pymssql.connect(
            server=os.getenv("DB_SERVER"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return {"success": True, "result": row[0]}
    except Exception as e:
        return {"error": str(e)}
