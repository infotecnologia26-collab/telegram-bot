import os
from pyrogram import Client, filters
from huggingface_hub import HfApi

# Credenciales y configuración desde variables de entorno
API_ID = int(os.getenv("TELEGRAM_API_ID", "33397479"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b59579dce7b909ff698dcfc5c3dd3268")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")

# Reemplaza esto con el nombre de tu Dataset en Hugging Face (ej. "Samas21/tu-dataset")
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "Samas21/telegram-files")

app = Client(
    "telegram_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

api = HfApi(token=HF_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("🤖 ¡Bot listo para recibir tus archivos y subirlos automáticamente a Hugging Face!")

@app.on_message(filters.document | filters.video)
async def handle_file(client, message):
    m = await message.reply_text("📥 Descargando archivo desde Telegram...")
    
    # Descargar el archivo localmente en el contenedor
    file_path = await message.download()
    file_name = os.path.basename(file_path)
    
    await m.edit_text("🚀 Subiendo archivo a Hugging Face Dataset...")
    
    try:
        # Subir a Hugging Face
        api.upload_file(
            path_or_fileobj=file_path,
            path_in_repo=file_name,
            repo_id=HF_DATASET_REPO,
            repo_type="dataset"
        )
        await m.edit_text(f"✅ ¡Archivo subido con éxito!\n📁 **Nombre:** `{file_name}`")
    except Exception as e:
        await m.edit_text(f"❌ Error al subir el archivo: {str(e)}")
    finally:
        # Limpiar el archivo local para liberar espacio en disco
        if os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    print("Iniciando bot de Telegram...")
    app.run()
