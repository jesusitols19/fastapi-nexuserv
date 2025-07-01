from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from azure.storage.blob import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from fastapi import Query
from typing import Optional
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
import random
import string

#Para enviar correo
import smtplib
from email.message import EmailMessage


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
        "https://witty-water-0b1d5eb10.1.azurestaticapps.net",
        "https://orange-field-0b261ba0f.2.azurestaticapps.net"
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
            INSERT INTO cvs (user_id, file_path, status_id, ia_result)
            VALUES (%s, %s, %s, %s)
        """, (user_id, blob_name, status_id, resultado))

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

@app.get("/cvs/detalle/{cv_id}")
def detalle_cv(cv_id: int):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT c.ia_result, c.file_path, u.first_name, u.last_name
            FROM cvs c
            JOIN users u ON u.id = c.user_id
            WHERE c.id = %s;
        """, (cv_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="CV no encontrado")

        return {
            "nombre": f"{row[2]} {row[3]}",
            "cv_path": row[1],
            "resultado_ia": row[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    


# Esquema opcional de respuesta
class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role_id: int
    phone_number: str | None = None
    document_number: str | None = None

@app.post("/auth/cliente", response_model=UserResponse)
def login_cliente(email: str = Form(...), password: str = Form(...)):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT 
            u.id,
            u.email,
            u.first_name,
            u.last_name,
            u.role_id,
            up.phone_number,
            ud.document_number
        FROM users u
        LEFT JOIN user_phones up ON up.user_id = u.id
        LEFT JOIN user_documents ud ON ud.user_id = u.id
        WHERE u.email = %s AND u.password = %s AND u.role_id = 1;
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
                "phone_number": user[5],
                "document_number": user[6]
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
    

@app.get("/cvs/estado/{estado}", response_model=List[CVConUsuario])
def get_cvs_por_estado(estado: str):
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
        WHERE s.name = %s;
        """
        cur.execute(query, (estado,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return [{
            "cv_id": row[0],
            "file_path": row[1],
            "uploaded_at": row[2].isoformat(),
            "user_id": row[3],
            "email": row[4],
            "first_name": row[5],
            "last_name": row[6]
        } for row in rows]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/usuarios")
def obtener_usuarios():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT 
            u.id, u.first_name, u.last_name, u.email, u.status, u.role_id,
            r.name AS role_name
        FROM users u
        JOIN roles r ON u.role_id = r.id;
        """
        cur.execute(query)
        users = cur.fetchall()

        # Obtener direcciones
        cur.execute("""
        SELECT user_id, address_text, latitude, longitude
        FROM user_addresses
        """)
        addresses = cur.fetchall()
        dir_map = {}
        for u_id, address, lat, lon in addresses:
            dir_map.setdefault(u_id, []).append(f"{address} ({lat}, {lon})")

        # Obtener teléfonos
        cur.execute("""
        SELECT user_id, phone_number FROM user_phones
        """)
        phones = cur.fetchall()
        phone_map = {}
        for u_id, phone in phones:
            phone_map.setdefault(u_id, []).append(phone)

        # Obtener documentos
        cur.execute("""
        SELECT user_id, document_number FROM user_documents
        """)
        docs = cur.fetchall()
        doc_map = {}
        for u_id, doc in docs:
            doc_map.setdefault(u_id, []).append(doc)

        result = []
        for u in users:
            result.append({
                "id": u[0],
                "first_name": u[1],
                "last_name": u[2],
                "email": u[3],
                "status": u[4],
                "role_id": u[5],
                "role_name": u[6],
                "phones": phone_map.get(u[0], []),
                "addresses": dir_map.get(u[0], []),
                "documents": doc_map.get(u[0], [])
            })

        cur.close()
        conn.close()
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/admin/usuarios/{user_id}/estado")
def cambiar_estado_usuario(user_id: int, estado: bool = Form(...)):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        cur.execute("UPDATE users SET status = %s WHERE id = %s", (estado, user_id))
        conn.commit()

        cur.close()
        conn.close()

        return {"message": f"Usuario {user_id} actualizado a estado {'Activo' if estado else 'Inactivo'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al actualizar estado: {str(e)}")

class PagoOut(BaseModel):
    id: int
    specialist_name: str
    client_name: str
    amount: float
    status: str
    created_at: str

@app.get("/admin/pagos", response_model=List[PagoOut])
def obtener_pagos():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT p.id,
                   CONCAT(s.first_name, ' ', s.last_name) AS specialist_name,
                   CONCAT(c.first_name, ' ', c.last_name) AS client_name,
                   p.amount, p.status, p.created_at
            FROM payments p
            JOIN users s ON p.specialist_id = s.id
            JOIN users c ON p.client_id = c.id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return [{
            "id": r[0],
            "specialist_name": r[1],
            "client_name": r[2],
            "amount": r[3],
            "status": r[4],
            "created_at": r[5].isoformat()
        } for r in rows]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    
@app.put("/admin/pagos/{pago_id}/estado")
def cambiar_estado_pago(pago_id: int, estado: str = Form(...)):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()
        cur.execute("UPDATE payments SET status = %s WHERE id = %s", (estado, pago_id))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": f"Pago {pago_id} actualizado a estado {estado}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/admin/solicitudes")
def obtener_solicitudes(
    status: Optional[str] = Query(None),
    acceptance_status: Optional[str] = Query(None)
):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        query = """
        SELECT
            sr.id,
            s.name AS service_name,
            CONCAT(c.first_name, ' ', c.last_name) AS client_name,
            CONCAT(e.first_name, ' ', e.last_name) AS specialist_name,
            sr.status,
            sr.acceptance_status,
            sr.requested_at
        FROM service_requests sr
        JOIN users c ON sr.user_id = c.id
        JOIN services s ON sr.service_id = s.id
        LEFT JOIN users e ON sr.specialist_id = e.id
        WHERE 1=1
        """
        params = []

        if status:
            query += " AND sr.status = %s"
            params.append(status)
        if acceptance_status:
            query += " AND sr.acceptance_status = %s"
            params.append(acceptance_status)

        cur.execute(query, params)
        rows = cur.fetchall()

        cur.close()
        conn.close()

        return [
            {
                "id": r[0],
                "service_name": r[1],
                "client_name": r[2],
                "specialist_name": r[3],
                "status": r[4],
                "acceptance_status": r[5],
                "requested_at": r[6].isoformat() if r[6] else None
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

def generar_password(nombre: str, apellido: str, length: int = 4) -> str:
    aleatorio = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    return f"{nombre.lower()}{apellido.lower()}{aleatorio}"

def enviar_correo(destinatario: str, asunto: str, cuerpo: str):
    remitente = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = remitente
    msg['To'] = destinatario
    msg.set_content(cuerpo)

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(remitente, password)
        smtp.send_message(msg)


@app.put("/postulantes/aceptar/{user_id}")
def aceptar_postulante(user_id: int):
    try:
        conn = get_pg_connection()
        cur = conn.cursor()

        # Obtener datos del usuario
        cur.execute("SELECT first_name, last_name, email FROM users WHERE id = %s AND role_id = 2;", (user_id,))
        user = cur.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Postulante no encontrado o ya no es postulante.")

        first_name, last_name, email = user
        nueva_pass = generar_password(first_name, last_name)

        # Actualizar usuario
        cur.execute("""
            UPDATE users 
            SET password = %s, role_id = 3 
            WHERE id = %s;
        """, (nueva_pass, user_id))
        conn.commit()

        # Enviar correo
        asunto = "Acceso como Especialista"
        cuerpo = f"""
Hola {first_name} {last_name},

Tu postulación ha sido aceptada. Ya puedes acceder a la plataforma movil como especialista.

Tus credenciales son:
- Usuario: {email}
- Contraseña: {nueva_pass}

Recuerda descargar la aplicacion desde google play.

Saludos,
Equipo Nexuserv
"""
        enviar_correo(email, asunto, cuerpo)

        cur.close()
        conn.close()

        return {"mensaje": "Postulante aceptado y correo enviado."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el servidor: {str(e)}")


# Modelo Pydantic para la validación de datos de servicio
class Service(BaseModel):
    name: str
    description: str = None
    image_url: str = None  # Agregar campo para la URL de la imagen

# Respuesta del servicio
class ServiceResponse(Service):
    id: int

# Endpoint para crear un servicio
@app.post("/services/", response_model=ServiceResponse)
async def create_service(service: Service):
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO services (name, description, image_url)
            VALUES (%s, %s, %s) RETURNING id, name, description, image_url;
        """, (service.name, service.description, service.image_url))
        new_service = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        return ServiceResponse(id=new_service[0], name=service.name, description=service.description, image_url=service.image_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear servicio: {e}")


# Endpoint para obtener todos los servicios
@app.get("/services/", response_model=List[ServiceResponse])
async def get_services():
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, description, image_url FROM services")
        services = cursor.fetchall()
        conn.commit()
        cursor.close()
        conn.close()
        return [ServiceResponse(id=row[0], name=row[1], description=row[2], image_url=row[3]) for row in services]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener servicios: {e}")


# Endpoint para actualizar un servicio
@app.put("/services/{service_id}", response_model=ServiceResponse)
async def update_service(service_id: int, service: Service):
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE services
            SET name = %s, description = %s, image_url = %s
            WHERE id = %s RETURNING id, name, description, image_url;
        """, (service.name, service.description, service.image_url, service_id))
        updated_service = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if updated_service:
            return ServiceResponse(id=updated_service[0], name=updated_service[1], description=updated_service[2], image_url=updated_service[3])
        else:
            raise HTTPException(status_code=404, detail="Servicio no encontrado")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al actualizar servicio: {e}")


# Endpoint para eliminar un servicio
@app.delete("/services/{service_id}", response_model=ServiceResponse)
async def delete_service(service_id: int):
    try:
        conn = get_pg_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM services WHERE id = %s RETURNING id, name, description, image_url;", (service_id,))
        deleted_service = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if deleted_service:
            return ServiceResponse(id=deleted_service[0], name=deleted_service[1], description=deleted_service[2], image_url=deleted_service[3])
        else:
            raise HTTPException(status_code=404, detail="Servicio no encontrado")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar servicio: {e}")





# Pruebas de conexion para probar Api

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
    

@app.get("/")
def root():
    return {"message": "¡Hola desde Azure!"}

@app.get("/test-email")
def test_email():
    try:
        enviar_correo("jesusitolspro.19@gmail.com", "Prueba desde FastAPI", "¡Correo de prueba enviado!")
        return {"message": "Correo enviado correctamente"}
    except Exception as e:
        return {"error": str(e)}