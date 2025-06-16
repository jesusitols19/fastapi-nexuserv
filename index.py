from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
import httpx
import fitz  # PyMuPDF
from dotenv import load_dotenv
load_dotenv()
import os
from openai import OpenAI  # ✅ NUEVA LIBRERÍA

# Usa la clave de OpenAI desde variable de entorno
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
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

    print(texto)

    # try:
    #     resultado = await analizar_con_gpt4o(texto)
    # except Exception as e:
    #     resultado = f"❌ Error al procesar el CV: {str(e)}"

    # print(resultado)

    # if resultado.strip().endswith("✅ Apto"):
    #     # Renombrar o confirmar guardado final si es apto
    #     final_ruta = f"uploads/{cv.filename}"
    #     os.rename(ruta, final_ruta)
    #     return {
    #         "usuario": usuario,
    #         "resultado_ia": resultado,
    #         "cv_guardado_en": final_ruta
    #     }
    # else:
    #     # Eliminar el temporal si no es apto
    #     os.remove(ruta)
    #     return {
    #         "usuario": usuario,
    #         "resultado_ia": resultado,
    #         "mensaje": "CV descartado por no cumplir con los requisitos"
    #     }

    return {
            "usuario": usuario,
            "resultado_ia": texto,
            "mensaje": "Xd"
        }
