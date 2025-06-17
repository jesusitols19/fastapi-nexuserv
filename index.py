from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from azure.storage.blob import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
import pyodbc
import httpx
import fitz  # PyMuPDF
from uuid import uuid4
from dotenv import load_dotenv
from datetime import datetime, timedelta
load_dotenv()
import os
import pymssql
from openai import OpenAI  # ✅ NUEVA LIBRERÍA


#Para postgres

from database_postgres import get_pg_connection
from pydantic import BaseModel
from typing import List


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
        "https://lemon-bush-042e64010.6.azurestaticapps.net",
        "https://witty-water-0b1d5eb10.1.azurestaticapps.net"
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


# @app.post("/postulaciones")
# async def crear_postulacion(
#     usuario: str = Form(...),
#     fecha_nacimiento: str = Form(...),
#     nombres: str = Form(...),
#     apellidos: str = Form(...),
#     correo: str = Form(...),
#     celular: str = Form(...),
#     dni: str = Form(...),
#     cv: UploadFile = File(...)
# ):
#     ruta = f"uploads/{cv.filename}"

#     with open(ruta, "wb") as f:
#         f.write(await cv.read())

#     texto = extraer_texto_pdf(ruta)

#     # Simulación del resultado de IA
#     try:
#         resultado = await analizar_con_gpt4o(texto)
#     except Exception as e:
#         os.remove(ruta)
#         resultado = f"❌ Error al procesar el CV: {str(e)}"

#     # Determinar estado
#     estado = "Apto" if resultado.strip().endswith("✅ Apto") else "No Apto"

#     # Subir a Azure Blob Storage
#     blob_name = f"{uuid4()}_{cv.filename}"
#     blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
#     with open(ruta, "rb") as data:
#         blob_client.upload_blob(data, overwrite=True)

#     os.remove(ruta)  # limpiar

#     # Insertar en base de datos usando pymssql
#     try:
#         conn = pymssql.connect(
#             server=os.getenv("DB_SERVER"),
#             user=os.getenv("DB_USER"),
#             password=os.getenv("DB_PASSWORD"),
#             database=os.getenv("DB_NAME")
#         )
#         cursor = conn.cursor()
#         cursor.execute("""
#             INSERT INTO postulaciones (usuario, nombres, apellidos, correo, celular, dni, fecha_nacimiento, cv_ruta, resultado_ia, estado)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """, (
#             usuario,
#             nombres,
#             apellidos,
#             correo,
#             celular,
#             dni,
#             datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date(),
#             blob_name,
#             resultado,
#             estado
#         ))
#         conn.commit()
#         cursor.close()
#         conn.close()
#     except Exception as db_error:
#         print(f"[ERROR SQL] {db_error}")
#         return {"error": f"No se pudo guardar en la base de datos: {str(db_error)}"}

#     return {
#         "usuario": usuario,
#         "estado": estado,
#         "ruta_en_blob": blob_name,
#         "resultado_ia": resultado
#     }


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

    # Guardar temporalmente el archivo
    with open(ruta, "wb") as f:
        f.write(await cv.read())

    texto = extraer_texto_pdf(ruta)

    # IA: resultado de análisis
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

    os.remove(ruta)

    # INSERT en PostgreSQL
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        # 1. Insertar usuario (tabla users)
        cur.execute("""
            INSERT INTO users (auth_provider, email, first_name, last_name, role_id, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, ("email", correo, nombres, apellidos, 2, True))
        user_id = cur.fetchone()[0]

        # 2. Insertar o recuperar id del estado en cv_statuses
        cur.execute("SELECT id FROM cv_statuses WHERE name = %s", (estado,))
        row = cur.fetchone()
        if row:
            status_id = row[0]
        else:
            cur.execute("INSERT INTO cv_statuses (name) VALUES (%s) RETURNING id", (estado,))
            status_id = cur.fetchone()[0]

        # 3. Insertar CV (tabla cvs)
        cur.execute("""
            INSERT INTO cvs (user_id, file_path, status_id)
            VALUES (%s, %s, %s)
        """, (user_id, blob_name, status_id))

        conn.commit()
        cur.close()
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
    

# @app.get("/postulaciones/apto")
# def obtener_postulantes_aptos():
#     try:

#         conn = pymssql.connect(
#             server=os.getenv("DB_SERVER"),
#             user=os.getenv("DB_USER"),
#             password=os.getenv("DB_PASSWORD"),
#             database=os.getenv("DB_NAME")
#         )
#         cursor = conn.cursor(as_dict=True)
#         cursor.execute("SELECT * FROM postulaciones WHERE estado = 'Apto'")
#         resultados = cursor.fetchall()
#         cursor.close()
#         conn.close()
#         return {"postulantes_aptos": resultados}
#     except Exception as e:
#         return {"error": f"Error al obtener postulantes aptos: {str(e)}"}

@app.get("/get-cv-url/{blob_name}")
def obtener_url_cv(blob_name: str):
    try:
        sas_token = generate_blob_sas(
            account_name=os.getenv("AZURE_STORAGE_ACCOUNT_NAME"),
            container_name=CONTAINER_NAME,
            blob_name=blob_name,
            account_key=os.getenv("AZURE_STORAGE_ACCOUNT_KEY"),
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(minutes=30)
        )
        url = f"https://{os.getenv('AZURE_STORAGE_ACCOUNT_NAME')}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}?{sas_token}"
        return {"url": url}
    except Exception as e:
        return {"error": str(e)}
    

@app.get("/test-pg")
def test_pg():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version = cur.fetchone()
        cur.close()
        conn.close()
        return {"pg_version": version[0]}
    except Exception as e:
        return {"error": f"PostgreSQL error: {str(e)}"}




# Esquema opcional de respuesta
class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role_id: int

@app.post("/auth/cliente", response_model=UserResponse)
def login_cliente(email: str = Form(...), password: str = Form(...)):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT id, email, first_name, last_name, role_id
        FROM users
        WHERE email = %s AND password = %s AND role_id = 1;
        """
        cur.execute(query, (email, password))
        user = cur.fetchone()

        cur.close()
        conn.close()

        if user:
            return {
                "id": user[0],
                "email": user[1],
                "first_name": user[2],
                "last_name": user[3],
                "role_id": user[4],
            }
        else:
            raise HTTPException(status_code=401, detail="Credenciales inválidas o rol incorrecto.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")
    

    
# Modelo de respuesta
class ServiceRequestOut(BaseModel):
    id: int
    service_name: str
    user_name: str
    service_details: str
    phone_number: str

@app.get("/service-requests/detalles", response_model=List[ServiceRequestOut])
def get_service_requests():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT
            sr.id,
            s.name AS service_name,
            CONCAT(u.first_name, ' ', u.last_name) AS user_name,
            sr.service_details,
            sr.phone_number
        FROM service_requests sr
        JOIN users u ON sr.user_id = u.id
        JOIN services s ON sr.service_id = s.id;
        """
        cur.execute(query)
        rows = cur.fetchall()

        cur.close()
        conn.close()

        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "service_name": row[1],
                "user_name": row[2],
                "service_details": row[3],
                "phone_number": row[4],
            })

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")
    


class CVConUsuario(BaseModel):
    cv_id: int
    file_path: str
    uploaded_at: str
    user_id: int
    email: str
    first_name: str
    last_name: str

@app.get("/cvs/apto", response_model=List[CVConUsuario])
def get_cvs_apto():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT 
            c.id AS cv_id,
            c.file_path,
            c.uploaded_at,
            u.id AS user_id,
            u.email,
            u.first_name,
            u.last_name
        FROM cvs c
        JOIN cv_statuses s ON c.status_id = s.id
        JOIN users u ON c.user_id = u.id
        WHERE s.name = 'Apto';
        """
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = []
        for row in rows:
            result.append({
                "cv_id": row[0],
                "file_path": row[1],
                "uploaded_at": row[2].isoformat(),
                "user_id": row[3],
                "email": row[4],
                "first_name": row[5],
                "last_name": row[6]
            })

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")
