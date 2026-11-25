import os
import time
import threading
import requests
from flask import Flask
from github import Github
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

# Cargar variables de entorno de Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_admin_raw = os.environ.get("ADMIN_ID", "0")
ADMIN_ID = int(_admin_raw) if _admin_raw.isdigit() else 0

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_DATASET = os.environ.get("HF_DATASET_REPO", "")

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

# --- 2. WORKER QUE LEE GITHUB, DESCARGA Y NOTIFICA A TELEGRAM ---
def worker_descargas():
    if not GITHUB_TOKEN or not GITHUB_REPO or not HF_TOKEN or not HF_DATASET:
        print("⚠️ Faltan variables de entorno de GitHub o Hugging Face en Render.")
        return

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
                tarea = lines[0].strip()
                restantes = "\n".join(lines[1:])

                partes = tarea.split("|")
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

                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(ruta_local, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)

                ruta_en_hf = f"{carpeta}/{nombre_archivo}"
                print(f"☁️ Subiendo a Hugging Face: {ruta_en_hf}...")
                hf_api.upload_file(
                    path_or_fileobj=ruta_local,
                    path_in_repo=ruta_en_hf,
                    repo_id=HF_DATASET,
                    repo_type="dataset"
                )

                os.remove(ruta_local)
                print(f"✅ Completado: {ruta_en_hf}")

                enlace_directo = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main/{carpeta}/{nombre_archivo}?token={HF_TOKEN}"
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
                            "text": f"🎉 **¡Video subido con éxito a Hugging Face!**\n\n📁 **Archivo:** `{nombre_archivo}`\n\n📋 **Copia este código para tu Blogger:**\n```html\n{codigo_html}\n```",
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
                print(f"⏳ Esperando tareas o revisando estado: {e}")
        
        time.sleep(15)

# --- 3. BOT DE TELEGRAM (FLUJO INTERACTIVO) ---
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

async def iniciar_conversacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    texto = update.message.text.strip()
    if not texto.startswith("http"):
        await update.message.reply_text("⚠️ Por favor, envíame un enlace válido que empiece por http.")
        return ConversationHandler.END

    # Guardar el enlace temporalmente en el contexto del usuario
    context.user_data['url_video'] = texto.split()[0]
    
    await update.message.reply_text(
        "📁 **¿En qué carpeta deseas guardar este video?**\n"
        "(Ejemplo: `peliculas`, `series`, `anime`, `doramas`):",
        parse_mode="Markdown"
    )
    return ESPERANDO_CARPETA

async def recibir_carpeta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    carpeta = update.message.text.strip().replace("/", "_")
    context.user_data['carpeta'] = carpeta

    await update.message.reply_text(
        f"✅ Carpeta seleccionada: `{carpeta}`\n\n"
        "🎬 **¿Qué nombre quieres ponerle al archivo final?**\n"
        "(Ejemplo: `capitulo_01.mp4` o `pelicula_completa.mp4`):",
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
    carpeta = context.user_data.get('carpeta', 'peliculas')

    await update.message.reply_text("⏳ Añadiendo tarea a la cola de GitHub...")
    agregar_a_github(chat_id, carpeta, url, nombre_archivo)

    await update.message.reply_text(
        f"✅ **¡Todo configurado con éxito!**\n"
        f"📁 Carpeta: `{carpeta}`\n"
        f"🎬 Archivo: `{nombre_archivo}`\n\n"
        f"El servidor lo descargará y te avisará cuando esté listo.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or ADMIN_ID == 0:
        print("❌ ERROR CRÍTICO: TELEGRAM_BOT_TOKEN o ADMIN_ID no están configurados en Render.")
    
    # Iniciar servicios en segundo plano
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=worker_descargas, daemon=True).start()
    
    print("🚀 Iniciando Bot de Telegram interactivo...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Manejador de conversación paso a paso
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex("^http"), iniciar_conversacion)],
        states={
            ESPERANDO_CARPETA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_carpeta)],
            ESPERANDO_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    app.add_handler(conv_handler)
    app.run_polling()
