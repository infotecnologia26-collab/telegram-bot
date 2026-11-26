import os
import time
import threading
import requests
from flask import Flask
from github import Github, Auth
from huggingface_hub import HfApi
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    ConversationHandler,
    filters
)

# --- CONFIGURACIÓN BLINDADA ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7963428989:AAFoZk...") 
_admin_raw = os.environ.get("ADMIN_ID", "0")
ADMIN_ID = int(_admin_raw) if str(_admin_raw).isdigit() else 0

# Token de GitHub configurado directamente
GITHUB_TOKEN = "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"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "infotecnologia26-collab/telegram-bot")

HF_TOKEN = os.environ.get("HF_TOKEN", "")
BASE_HF_REPO = os.environ.get("HF_DATASET_REPO", "Samas21/bot")
HF_USER = BASE_HF_REPO.split("/")[0] if "/" in BASE_HF_REPO else "Samas21"

# Estados para la conversación interactiva
ESPERANDO_CARPETA, ESPERANDO_NOMBRE = range(2)

# --- 1. SERVIDOR WEB PARA RENDER ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot y Worker activos correctamente en Render."

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- 2. WORKER QUE DESCARGA Y SUBE ARCHIVOS PESADOS A HUGGING FACE ---
def worker_descargas():
    if not GITHUB_TOKEN or not HF_TOKEN:
        print("⚠️ Faltan tokens de GitHub o Hugging Face.")
        return

    try:
        auth = Auth.Token(GITHUB_TOKEN)
        g = Github(auth=auth)
        repo = g.get_repo(GITHUB_REPO)
    except Exception as e:
        print(f"❌ Error al autenticar en GitHub: {e}")
        return

    hf_api = HfApi(token=HF_TOKEN)
    os.makedirs("temp_downloads", exist_ok=True)

    while True:
        try:
            file = repo.get_contents("queue.txt")
            content = file.decoded_content.decode("utf-8").strip()
            
            if content:
                lines = content.splitlines()
                tarea = lines[0].strip()
                restantes = "\n".join(lines[1:])

                partes = tarea.split("|")
                if len(partes) >= 4:
                    chat_id = int(partes[0])
                    destino_repo = partes[1].strip()
                    url = partes[2]
                    nombre_archivo = partes[3]
                else:
                    chat_id = ADMIN_ID if ADMIN_ID != 0 else int(partes[0])
                    destino_repo = partes[0]
                    url = partes[1]
                    nombre_archivo = partes[2] if len(partes) > 2 else url.split("/")[-1].split("?")[0]
                
                if not nombre_archivo.endswith(".mp4"):
                    nombre_archivo += ".mp4"

                ruta_local = os.path.join("temp_downloads", nombre_archivo)
                print(f"📥 Descargando archivo pesado para '{HF_USER}/{destino_repo}'...")

                with requests.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(ruta_local, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)

                repo_id_destino = f"{HF_USER}/{destino_repo}"
                print(f"☁️ Subiendo archivo pesado a Hugging Face ({repo_id_destino})...")
                
                hf_api.upload_file(
                    path_or_fileobj=ruta_local,
                    path_in_repo=nombre_archivo,
                    repo_id=repo_id_destino,
                    repo_type="dataset"
                )

                os.remove(ruta_local)
                print(f"✅ ¡Subida completada en {repo_id_destino}!")

                enlace_directo = f"https://huggingface.co/datasets/{repo_id_destino}/resolve/main/{nombre_archivo}?token={HF_TOKEN}"
                codigo_html = (
                    f'<div style="position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; background: #000; border-radius: 8px;">\n'
                    f'    <video controls="" playsinline="" width="100%" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%;">\n'
                    f'        <source src="{enlace_directo}" type="video/mp4">\n'
                    f'        Tu navegador no soporta el elemento de video.\n'
                    f'    </video>\n'
                    f'</div>'
                )

                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": f"🎉 **¡Video de 3.4 GB subido con éxito!**\n\n📦 **Dataset:** `{repo_id_destino}`\n🎬 **Archivo:** `{nombre_archivo}`\n\n📋 **Copia este código para tu Blogger:**\n```html\n{codigo_html}\n```",
                            "parse_mode": "Markdown"
                        }
                    )
                except Exception as e:
                    print(f"No se pudo enviar la notificación a Telegram: {e}")

                if restantes.strip():
                    repo.update_file(file.path, f"Procesado {nombre_archivo}", restantes, file.sha)
                else:
                    repo.delete_file(file.path, "Cola vaciada", file.sha)

        except Exception as e:
            if "404" not in str(e):
                print(f"⏳ Procesando o esperando cola: {e}")
        
        time.sleep(15)

# --- 3. BOT DE TELEGRAM INTERACTIVO ---
def agregar_a_github(chat_id, destino_repo, url, nombre_archivo):
    auth = Auth.Token(GITHUB_TOKEN)
    g = Github(auth=auth)
    repo = g.get_repo(GITHUB_REPO)
    nueva_linea = f"{chat_id}|{destino_repo}|{url}|{nombre_archivo}"
    
    try:
        file = repo.get_contents("queue.txt")
        contenido_actual = file.decoded_content.decode("utf-8").strip()
        nuevo_contenido = f"{contenido_actual}\n{nueva_linea}" if contenido_actual else nueva_linea
        repo.update_file(file.path, f"Añadir {nombre_archivo}", nuevo_contenido, file.sha)
    except Exception:
        repo.create_file("queue.txt", f"Crear cola con {nombre_archivo}", nueva_linea)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_ID
    if ADMIN_ID == 0:
        ADMIN_ID = update.effective_user.id

    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "👋 ¡Hola Admin! Bot listo para procesar archivos pesados.\n\n"
        "🎬 **Envíame un enlace de video (http...)** para elegir a qué repositorio de Hugging Face enviarlo.",
        parse_mode="Markdown"
    )

async def iniciar_conversacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_ID
    if ADMIN_ID == 0:
        ADMIN_ID = update.effective_user.id

    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    texto = update.message.text.strip()
    if not texto.startswith("http"):
        await update.message.reply_text("⚠️ Por favor, envíame un enlace válido que empiece por http.")
        return ConversationHandler.END

    context.user_data['url_video'] = texto.split()[0]
    
    await update.message.reply_text(
        f"📦 **¿A qué repositorio de Hugging Face quieres subirlo?**\n"
        f"*(Ejemplo: `P311`, `bêt`, `Mu51c`)*\n\n"
        f"Se subirá a: `{HF_USER}/[tu_respuesta]`",
        parse_mode="Markdown"
    )
    return ESPERANDO_CARPETA

async def recibir_carpeta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    destino_repo = update.message.text.strip().replace(" ", "-")
    context.user_data['destino_repo'] = destino_repo

    await update.message.reply_text(
        f"✅ Repositorio seleccionado: `{HF_USER}/{destino_repo}`\n\n"
        "🎬 **¿Qué nombre quieres ponerle al archivo final?**\n"
        "(Ejemplo: `pelicula.mp4`):",
        parse_mode="Markdown"
    )
    return ESPERANDO_NOMBRE

async def recibir_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    nombre_archivo = update.message.text.strip()
    if not nombre_archivo.endswith(".mp4"):
        nombre_archivo += ".mp4"

    chat_id = update.effective_chat.id
    url = context.user_data.get('url_video')
    destino_repo = context.user_data.get('destino_repo', 'bot')

    await update.message.reply_text("⏳ Añadiendo tarea a la cola de GitHub...")
    agregar_a_github(chat_id, destino_repo, url, nombre_archivo)

    await update.message.reply_text(
        f"✅ **¡Tarea registrada!**\n"
        f"📦 Destino: `{HF_USER}/{destino_repo}`\n"
        f"🎬 Archivo: `{nombre_archivo}`\n\n"
        f"El bot descargará los 3.4 GB y los subirá a Hugging Face en segundo plano. Te avisaré cuando termine.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=worker_descargas, daemon=True).start()
    
    print("🚀 Iniciando Bot de Telegram para archivos pesados...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex("^http"), iniciar_conversacion)],
        states={
            ESPERANDO_CARPETA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_carpeta)],
            ESPERANDO_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv_handler)
    app.run_polling()
