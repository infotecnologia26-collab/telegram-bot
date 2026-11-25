import os
import time
import threading
import requests
from flask import Flask
from github import Github
from huggingface_hub import HfApi
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Cargar variables de entorno configuradas en Secrets
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
HF_TOKEN = os.environ.get("HF_TOKEN")
HF_DATASET = os.environ.get("HF_DATASET")

# Carpeta por defecto en Hugging Face
CARPETA_ACTUAL = "peliculas"

# --- 1. SERVIDOR WEB PARA MANTENER VIVO EL SPACE ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot y Worker activos correctamente."

def run_web():
    web_app.run(host="0.0.0.0", port=7860)

# --- 2. WORKER QUE LEE GITHUB, DESCARGA Y NOTIFICA A TELEGRAM ---
def worker_descargas():
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    hf_api = HfApi(token=HF_TOKEN)
    os.makedirs("temp_downloads", exist_ok=True)

    while True:
        try:
            file = repo.get_contents("queue.txt")
            content = file.decoded_content.decode("utf-8").strip()
            
            if content:
                lines = content.splitlines()
                # Formato esperado en queue.txt: chat_id|carpeta|url|nombre_archivo
                tarea = lines[0].strip()
                restantes = "\n".join(lines[1:])

                partes = tarea.split("|")
                # Soporte para versiones anteriores sin chat_id o con chat_id
                if len(partes) >= 4:
                    chat_id = int(partes[0])
                    carpeta = partes[1]
                    url = partes[2]
                    nombre_archivo = partes[3]
                else:
                    chat_id = ADMIN_ID
                    carpeta = partes[0]
                    url = partes[1]
                    nombre_archivo = partes[2] if len(partes) > 2 else url.split("/")[-1].split("?")[0]
                
                if not nombre_archivo.endswith(".mp4"):
                    nombre_archivo += ".mp4"

                ruta_local = os.path.join("temp_downloads", nombre_archivo)
                print(f"📥 Descargando {nombre_archivo} para la carpeta '{carpeta}'...")

                # Descargar archivo por partes (soporta archivos pesados)
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(ruta_local, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)

                # Subir el archivo descargado a la carpeta elegida en el Dataset de Hugging Face
                ruta_en_hf = f"{carpeta}/{nombre_archivo}"
                print(f"☁️ Subiendo a Hugging Face: {ruta_en_hf}...")
                hf_api.upload_file(
                    path_or_fileobj=ruta_local,
                    path_in_repo=ruta_en_hf,
                    repo_id=HF_DATASET,
                    repo_type="dataset"
                )

                # Borrar archivo temporal del Space para no llenar la memoria
                os.remove(ruta_local)
                print(f"✅ Completado: {ruta_en_hf}")

                # Generar enlace directo y código HTML para Blogger
                enlace_directo = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main/{carpeta}/{nombre_archivo}?token={HF_TOKEN}"
                codigo_html = (
                    f'<div style="position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; background: #000; border-radius: 8px;">\n'
                    f'    <video controls="" playsinline="" width="100%" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">\n'
                    f'        <source src="{enlace_directo}" type="video/mp4">\n'
                    f'        Tu navegador no soporta el elemento de video.\n'
                    f'    </video>\n'
                    f'</div>'
                )

                # Enviar notificación a Telegram con el código listo
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": f"🎉 **¡Video subido con éxito a Hugging Face!**\n\n📁 **Archivo:** `{nombre_archivo}`\n\n📋 **Copia este código para tu Blogger:**\n```html\n{codigo_html}\n```",
                            "parse_mode": "Markdown"
                        }
                    )
                except Exception as e:
                    print(f"No se pudo enviar la notificación a Telegram: {e}")

                # Actualizar o borrar queue.txt en GitHub
                if restantes.strip():
                    repo.update_file(file.path, f"Procesado {nombre_archivo}", restantes, file.sha)
                else:
                    repo.delete_file(file.path, "Cola vaciada", file.sha)

        except Exception as e:
            if "404" not in str(e):
                print(f"⏳ Esperando tareas o revisando estado: {e}")
        
        time.sleep(15)

# --- 3. BOT DE TELEGRAM ---
def agregar_a_github(chat_id, carpeta, url, nombre_archivo):
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    nueva_linea = f"{chat_id}|{carpeta}|{url}|{nombre_archivo}"
    
    try:
        file = repo.get_contents("queue.txt")
        contenido_actual = file.decoded_content.decode("utf-8").strip()
        nuevo_contenido = f"{contenido_actual}\n{nueva_linea}" if contenido_actual else nueva_linea
        repo.update_file(file.path, f"Añadir {nombre_archivo}", nuevo_contenido, file.sha)
    except Exception:
        repo.create_file("queue.txt", f"Crear cola con {nombre_archivo}", nueva_linea)

async def cmd_carpeta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CARPETA_ACTUAL
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text(f"📁 Carpeta actual: `{CARPETA_ACTUAL}`\n\nPara cambiarla escribe: `/carpeta series`", parse_mode="Markdown")
        return
    CARPETA_ACTUAL = context.args[0].strip().replace("/", "_")
    await update.message.reply_text(f"✅ Carpeta cambiada a: `{CARPETA_ACTUAL}`", parse_mode="Markdown")

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    chat_id = update.effective_chat.id

    # Caso A: El usuario envía un enlace directo
    if update.message.text and update.message.text.startswith("http"):
        partes = update.message.text.split()
        url = partes[0].strip()
        nombre = partes[1].strip() if len(partes) > 1 else url.split("/")[-1].split("?")[0]
        if not nombre:
            nombre = f"video_{int(time.time())}.mp4"
            
        await update.message.reply_text("⏳ Enviando enlace a GitHub y procesando descarga...")
        agregar_a_github(chat_id, CARPETA_ACTUAL, url, nombre)
        await update.message.reply_text(
            f"✅ **En cola de descarga**\n"
            f"📁 Carpeta destino: `{CARPETA_ACTUAL}`\n"
            f"🎬 Archivo: `{nombre}`\n\n"
            f"Te avisaré por aquí en cuanto termine de subirlo a Hugging Face.",
            parse_mode="Markdown"
        )

    # Caso B: El usuario reenvía o sube un archivo MP4 directo al bot
    elif update.message.video or update.message.document:
        archivo = update.message.video or update.message.document
        nombre = archivo.file_name or f"video_{int(time.time())}.mp4"
        try:
            tg_file = await context.bot.get_file(archivo.file_id)
            url_directa = tg_file.file_path
            agregar_a_github(chat_id, CARPETA_ACTUAL, url_directa, nombre)
            await update.message.reply_text(
                f"✅ **Video en cola**\n"
                f"📁 Carpeta destino: `{CARPETA_ACTUAL}`\n"
                f"🎬 Archivo: `{nombre}`\n\n"
                f"Te avisaré cuando esté listo.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Telegram limita los archivos reenviados directamente al bot a 20 MB.\n"
                f"Para archivos grandes, envíame el enlace directo de descarga.\n(Detalle: {e})"
            )

if __name__ == "__main__":
    # Iniciar servidor web en segundo plano
    threading.Thread(target=run_web, daemon=True).start()
    # Iniciar worker de descargas en segundo plano
    threading.Thread(target=worker_descargas, daemon=True).start()
    
    # Iniciar Bot de Telegram
    print("🚀 Iniciando Bot de Telegram...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("carpeta", cmd_carpeta))
    app.add_handler(MessageHandler((filters.TEXT | filters.VIDEO | filters.Document.ALL) & (~filters.COMMAND), manejar_mensaje))
    app.run_polling()
