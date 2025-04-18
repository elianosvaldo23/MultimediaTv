import logging
import asyncio
import re
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError
from database import Database
from plans import PLANS
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from flask import Flask
from threading import Thread
from functools import wraps
from bs4 import BeautifulSoup
from io import BytesIO
from telegram import Update, InputFile
from telegram.constants import ParseMode, ChatAction
from imdb import IMDb
import os
import tempfile
import traceback
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse

# Mantener el bot activo en Render
app = Flask('')

@app.route('/')
def home():
    return "¡El bot está activo!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Constantes del bot
TOKEN = "7853962859:AAEsWR8uuqey8zh62XnFDlXmjDZzaNiO_YA"
ADMIN_ID = 1742433244
CHANNEL_ID = -1002584219284
GROUP_ID = -1002585538833
SEARCH_CHANNEL_ID = -1002302159104

# Add this at the top with other constants
PLANS_INFO = {
    'basic': {
        'name': 'Plan Básico',
        'price': 'Gratis',
        'searches_per_day': 3,
        'requests_per_day': 1,
        'can_forward': False,
        'duration_days': None  # No expiration
    },
    'pro': {
        'name': 'Plan Pro',
        'price': '169.99 CUP / 0.49 USD',
        'searches_per_day': 15,
        'requests_per_day': 2,
        'can_forward': False,
        'duration_days': 30,
        'features': ['15 búsquedas diarias', '2 pedidos diarios', 'No puede reenviar contenido ni guardarlo', 'Duración: 30 días']
    },
    'plus': {
        'name': 'Plan Plus',
        'price': '649.99 CUP / 1.99 USD',
        'searches_per_day': 50,
        'requests_per_day': 10,
        'can_forward': True,
        'duration_days': 30,
        'features': ['50 búsquedas diarias', '10 pedidos diarios', 'Soporte prioritario', 'Enlaces directos de descarga', 'Duración: 30 días']
    },
    'ultra': {
        'name': 'Plan Ultra',
        'price': '1049.99 CUP / 2.99 USD',
        'searches_per_day': float('inf'),  # Unlimited
        'requests_per_day': float('inf'),  # Unlimited
        'can_forward': True,
        'duration_days': 30,
        'features': ['Búsquedas ilimitadas', 'Pedidos ilimitados', 'Reenvío y guardado permitido', 'Enlaces directos de descarga', 'Soporte VIP', 'Duración: 30 días']
    }
}

# Constantes y variables globales para el sistema de series
UPSER_STATE_IDLE = 0        # No hay carga de serie en proceso
UPSER_STATE_RECEIVING = 1   # Recibiendo capítulos
UPSER_STATE_COVER = 2       # Esperando la portada con descripción

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Crear una instancia de IMDb
ia = IMDb()

# Initialize database
db = Database()

# Store the latest message ID
last_message_id = 0

# Cache for message content to avoid repeated requests
message_cache = {}

# Cache for search results to speed up repeated searches
search_cache = {}

# Cache expiration time (in seconds)
CACHE_EXPIRATION = 3600  # 1 hour

# Maximum number of messages to search
MAX_SEARCH_MESSAGES = 1000

# Maximum number of results to show
MAX_RESULTS = 10

# User preferences (store user settings)
user_preferences = {}

# Función para verificar membresía al canal
async def is_channel_member(user_id, context):
    """Verifica si un usuario es miembro del canal principal."""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"Error verificando membresía: {e}")
        return False
        
        # Decorador para verificar membresía en el canal
def check_channel_membership(func):
    """Decorador para verificar la membresía al canal antes de ejecutar comandos."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_chat.type == 'private':  # Solo verificar en chats privados
            user_id = update.effective_user.id
            
            # Verificar si el usuario ya está marcado como verificado recientemente (caché)
            verification_cache = context.bot_data.setdefault('verification_cache', {})
            cached_until = verification_cache.get(user_id)
            
            current_time = datetime.now()
            if cached_until and current_time < cached_until:
                # El usuario está en caché y la verificación es válida
                return await func(update, context, *args, **kwargs)
            
            # Verificar membresía
            is_member = await is_channel_member(user_id, context)
            
            if not is_member:
                # Usuario no es miembro, mostrar mensaje de suscripción
                keyboard = [
                    [InlineKeyboardButton("Unirse al Canal 📢", url=f"https://t.me/multimediatvOficial")],
                    [InlineKeyboardButton("Ya me uní ✅", callback_data="verify_membership")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.effective_message.reply_text(
                    "⚠️ Para usar el bot, debes unirte a nuestro canal principal.\n\n"
                    "1. Haz clic en el botón 'Unirse al Canal 📢'\n"
                    "2. Una vez unido, vuelve aquí y presiona 'Ya me uní ✅'",
                    reply_markup=reply_markup
                )
                return
            
            # Si llega aquí, el usuario es miembro, lo guardamos en caché para reducir verificaciones
            # Caché por 30 minutos
            verification_cache[user_id] = current_time + timedelta(minutes=30)
            
        # Si es un chat grupal o el usuario es miembro, ejecutar la función original
        return await func(update, context, *args, **kwargs)
    
    return wrapper

@check_channel_membership
# Lógica para el bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el comando /start."""
    if update.message is None:
        return
        
    user = update.effective_user
    
    # Comprobar si es una solicitud de contenido específico
    if context.args and context.args[0].startswith('content_'):
        try:
            content_id = int(context.args[0].replace('content_', ''))
            user_data = db.get_user(user.id)
            can_forward = user_data and user_data.get('can_forward', False)
            
            # Mostrar acción de escribiendo mientras se procesa
            await context.bot.send_chat_action(
                chat_id=update.message.chat_id,
                action=ChatAction.TYPING
            )
            
            try:
                # Usar siempre copy_message con permisos diferentes
                await context.bot.copy_message(
                    chat_id=update.message.chat_id,
                    from_chat_id=SEARCH_CHANNEL_ID,
                    message_id=content_id,
                    protect_content=not can_forward  # False para Plus/Ultra, True para Básico/Pro
                )
                
                # Incrementar contador de búsquedas diarias
                db.increment_daily_usage(user.id)
                
                return  # Salir de la función después de enviar el contenido
            except Exception as e:
                logger.error(f"Error al enviar contenido específico: {e}")
                await update.message.reply_text(
                    "❌ No se pudo cargar el contenido solicitado. Es posible que ya no esté disponible."
                )
                # Continuar con el flujo normal de start si falla
        except (ValueError, IndexError) as e:
            logger.error(f"Error procesando content_id: {e}")
            # Continuar con el flujo normal de start si falla
    
    # Comprobar si es una solicitud de serie
    if context.args and context.args[0].startswith('series_'):
        try:
            series_id = int(context.args[0].replace('series_', ''))
            await handle_series_request(update, context, series_id)
            return
        except (ValueError, IndexError) as e:
            logger.error(f"Error procesando series_id: {e}")
            # Continuar con el flujo normal de start si falla
    
    # Check if this is a referral (código existente)
    if context.args and context.args[0].startswith('ref_'):
        ref_id = context.args[0].replace('ref_', '')
        try:
            ref_id = int(ref_id)
            if ref_id != user.id and db.user_exists(ref_id):
                # Add referral if not already added
                if not db.is_referred(user.id):
                    db.add_referral(ref_id, user.id)
                    await context.bot.send_message(
                        chat_id=ref_id,
                        text=f"¡Nuevo referido! {user.first_name} se ha unido usando tu enlace. Has ganado +1 💎"
                    )
        except ValueError:
            pass

    # Resto del código original de start...
    user_data = db.get_user(user.id)
    if not user_data:  # Registrar solo si el usuario no existe
        db.add_user(
            user.id,
            user.username,
            user.first_name,
            user.last_name
        )
    
    # Create main menu keyboard
    keyboard = [
        [
            InlineKeyboardButton("Multimedia Tv 📺", url=f"https://t.me/multimediatvOficial"),
            InlineKeyboardButton("Pedidos 📡", url=f"https://t.me/+X9S4pxF8c7plYjYx")
        ],
        [InlineKeyboardButton("Perfil 👤", callback_data="profile")],
        [InlineKeyboardButton("Planes 📜", callback_data="plans")],
        [InlineKeyboardButton("Información 📰", callback_data="info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"¡Hola! {user.first_name}👋 te doy la bienvenida\n\n"
        f"MultimediaTv un bot donde encontraras un amplio catálogo de películas y series, "
        f"las cuales puedes buscar o solicitar en caso de no estar en el catálogo",
        reply_markup=reply_markup
    )

async def handle_series_request(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: int) -> None:
    """Manejar la solicitud de visualización de una serie"""
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)
    
    # Verificar límites de búsqueda
    if not db.increment_daily_usage(user_id):
        # Mostrar mensaje de límite excedido y opciones de planes
        keyboard = []
        for plan_id, plan in PLANS_INFO.items():
            if plan_id != 'basic':
                keyboard.append([
                    InlineKeyboardButton(
                        f"{plan['name']} - {plan['price']}",
                        callback_data=f"buy_plan_{plan_id}"
                    )
                ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Has alcanzado tu límite de búsquedas diarias.\n\n"
            "Para continuar viendo series, adquiere un plan premium:",
            reply_markup=reply_markup
        )
        return
    
    # Mostrar acción de escribiendo
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    
    # Obtener datos de la serie
    series = db.get_series(series_id)
    
    if not series:
        await update.message.reply_text(
            "❌ Serie no encontrada. Es posible que haya sido eliminada o que el enlace sea incorrecto."
        )
        return
    
    # Obtener capítulos
    episodes = db.get_series_episodes(series_id)
    
    if not episodes:
        await update.message.reply_text(
            "❌ Esta serie no tiene capítulos disponibles actualmente."
        )
        return
    
    # Obtener portada
    cover_message_id = series['cover_message_id']
    
    try:
        # Enviar portada
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=SEARCH_CHANNEL_ID,
            message_id=cover_message_id
        )
        
        # Crear botones para los capítulos
        keyboard = []
        
        # Añadir un botón para cada capítulo, organizados en filas de 3
        for i in range(0, len(episodes), 3):
            row = []
            for j in range(i, min(i + 3, len(episodes))):
                episode = episodes[j]
                row.append(
                    InlineKeyboardButton(
                        f"Capítulo {episode['episode_number']}",
                        callback_data=f"ep_{series_id}_{episode['episode_number']}"
                    )
                )
            keyboard.append(row)
        
        # Añadir botón para enviar todos los capítulos
        keyboard.append([
            InlineKeyboardButton(
                "Enviar todos los capítulos",
                callback_data=f"ep_all_{series_id}"
            )
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Enviar mensaje con botones
        await update.message.reply_text(
            f"📺 *{series['title']}*\n\n"
            f"Selecciona un capítulo para ver o solicita todos los capítulos:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Error enviando datos de serie: {e}")
        await update.message.reply_text(
            f"❌ Error al mostrar la serie: {str(e)[:100]}\n\n"
            f"Por favor, intenta más tarde."
        )

async def send_episode(query, context, series_id, episode_number):
    """Enviar un capítulo específico al usuario"""
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    can_forward = user_data and user_data.get('can_forward', False)
    
    await query.answer("Procesando tu solicitud...")
    
    # Mostrar acción de escribiendo
    await context.bot.send_chat_action(
        chat_id=query.message.chat_id,
        action=ChatAction.TYPING
    )
    
    try:
        # Obtener datos del capítulo
        episode = db.get_episode(series_id, episode_number)
        
        if not episode:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Capítulo {episode_number} no encontrado."
            )
            return
        
        # Enviar el capítulo
        await context.bot.copy_message(
            chat_id=query.message.chat_id,
            from_chat_id=SEARCH_CHANNEL_ID,
            message_id=episode['message_id'],
            protect_content=not can_forward  # Proteger según el plan
        )
        
        # Marcar el botón como seleccionado
        keyboard = query.message.reply_markup.inline_keyboard
        new_keyboard = []
        
        for row in keyboard:
            new_row = []
            for button in row:
                if button.callback_data == query.data:
                    # Marcar este botón como seleccionado
                    new_row.append(InlineKeyboardButton(
                        f"✅ {button.text}",
                        callback_data=button.callback_data
                    ))
                else:
                    new_row.append(button)
            new_keyboard.append(new_row)
        
        # Actualizar el mensaje con el nuevo teclado
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(new_keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error enviando capítulo: {e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ Error al enviar el capítulo: {str(e)[:100]}"
        )

async def send_all_episodes(query, context, series_id):
    """Enviar todos los capítulos de una serie al usuario"""
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    can_forward = user_data and user_data.get('can_forward', False)
    
    await query.answer("Enviando todos los capítulos...")
    
    # Mostrar acción de escribiendo
    await context.bot.send_chat_action(
        chat_id=query.message.chat_id,
        action=ChatAction.TYPING
    )
    
    try:
        # Obtener todos los capítulos
        episodes = db.get_series_episodes(series_id)
        
        if not episodes:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ No se encontraron capítulos para esta serie."
            )
            return
        
        # Enviar mensaje de inicio
        status_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"⏳ Enviando {len(episodes)} capítulos... Por favor, espera."
        )
        
        # Enviar cada capítulo
        for i, episode in enumerate(episodes):
            try:
                # Actualizar estado periódicamente
                if i % 5 == 0 and i > 0:
                    await status_message.edit_text(
                        f"⏳ Enviando capítulos... ({i}/{len(episodes)})"
                    )
                
                # Enviar capítulo
                await context.bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=SEARCH_CHANNEL_ID,
                    message_id=episode['message_id'],
                    protect_content=not can_forward,  # Proteger según el plan
                    disable_notification=(i < len(episodes) - 1)  # Solo notificar el último
                )
                
                # Pequeña pausa para no sobrecargar
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error enviando capítulo {i+1}: {e}")
                continue
        
        # Actualizar mensaje de estado
        await status_message.edit_text(
            f"✅ Se han enviado todos los capítulos ({len(episodes)})."
        )
        
        # Marcar el botón como seleccionado
        keyboard = query.message.reply_markup.inline_keyboard
        new_keyboard = []
        
        for row in keyboard:
            new_row = []
            for button in row:
                if button.callback_data == query.data:
                    # Marcar este botón como seleccionado
                    new_row.append(InlineKeyboardButton(
                        f"✅ {button.text}",
                        callback_data=button.callback_data
                    ))
                else:
                    new_row.append(button)
            new_keyboard.append(new_row)
        
        # Actualizar el mensaje con el nuevo teclado
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(new_keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error general enviando todos los capítulos: {e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ Error al enviar los capítulos: {str(e)[:100]}"
        )

@check_channel_membership
async def imdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga y envía información de una película/serie desde un enlace de IMDb."""
    if not update.message:
        return
    
    # Verificar si el usuario proporcionó un enlace
    if not context.args:
        await update.message.reply_text(
            "Por favor, proporciona un enlace de IMDb.\n"
            "Ejemplo: /imdb https://www.imdb.com/title/tt14513804/"
        )
        return
    
    # Obtener el enlace
    imdb_url = context.args[0]
    
    # Mostrar acción de escribiendo mientras se procesa
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    
    # Verificar que es un enlace de IMDb válido
    if not re.match(r'https?://(www\.)?imdb\.com/title/tt\d+/?.*', imdb_url):
        await update.message.reply_text(
            "❌ El enlace proporcionado no es un enlace válido de IMDb.\n"
            "Debe tener el formato: https://www.imdb.com/title/tt??????/"
        )
        return
    
    # Extraer el ID de IMDb del enlace
    imdb_id = re.search(r'tt\d+', imdb_url).group(0)
    
    try:
        # Enviar mensaje de procesamiento
        processing_msg = await update.message.reply_text(
            "🔍 Procesando información de IMDb... Por favor espera."
        )
        
        # Método 1: Usar IMDbPY para obtener información
        try:
            # Obtener la película/serie por ID
            movie = ia.get_movie(imdb_id[2:])  # Eliminar 'tt' del ID
            
            # Extraer información básica
            title = movie.get('title', 'Título no disponible')
            year = movie.get('year', 'Año no disponible')
            rating = movie.get('rating', 'N/A')
            genres = ', '.join(movie.get('genres', ['Género no disponible']))
            plot = movie.get('plot outline', 'Sinopsis no disponible')
            
            # Obtener directores
            directors = []
            if 'directors' in movie:
                directors = [director['name'] for director in movie['directors'][:3]]
            directors_str = ', '.join(directors) if directors else 'No disponible'
            
            # Obtener actores principales
            cast = []
            if 'cast' in movie:
                cast = [actor['name'] for actor in movie['cast'][:5]]
            cast_str = ', '.join(cast) if cast else 'No disponible'
            
            # Construir mensaje
            message = (
                f"🎬 *{title}* ({year})\n\n"
                f"⭐ *Calificación:* {rating}/10\n"
                f"🎭 *Género:* {genres}\n"
                f"🎬 *Director:* {directors_str}\n"
                f"👥 *Reparto principal:* {cast_str}\n\n"
                f"📝 *Sinopsis:*\n{plot}\n\n"
                f"🔗 [Ver en IMDb]({imdb_url})"
            )
            
            # Obtener URL del póster si está disponible
            poster_url = None
            if 'cover url' in movie:
                poster_url = movie['cover url']
            
        except Exception as e:
            logger.error(f"Error usando IMDbPY: {e}")
            
            # Si falla IMDbPY, usar web scraping como método alternativo
            try:
                # Realizar la solicitud HTTP
                response = requests.get(imdb_url, headers={'User-Agent': 'Mozilla/5.0'})
                response.raise_for_status()  # Verificar que la solicitud fue exitosa
                
                # Parsear el HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extraer información básica
                title_elem = soup.select_one('h1')
                title = title_elem.text.strip() if title_elem else 'Título no disponible'
                
                # Intentar obtener el año
                year_elem = soup.select_one('span.TitleBlockMetaData__ListItemText-sc-12ein40-2')
                year = year_elem.text.strip() if year_elem else 'Año no disponible'
                
                # Intentar obtener la calificación
                rating_elem = soup.select_one('span.AggregateRatingButton__RatingScore-sc-1ll29m0-1')
                rating = rating_elem.text.strip() if rating_elem else 'N/A'
                
                # Intentar obtener géneros
                genres_elems = soup.select('span.ipc-chip__text')
                genres = ', '.join([genre.text for genre in genres_elems[:3]]) if genres_elems else 'Género no disponible'
                
                # Intentar obtener la sinopsis
                plot_elem = soup.select_one('span.GenresAndPlot__TextContainerBreakpointXL-sc-cum89p-2')
                plot = plot_elem.text.strip() if plot_elem else 'Sinopsis no disponible'
                
                # Intentar obtener directores y reparto
                credits_elems = soup.select('a.StyledComponents__ActorName-sc-y9ygcu-1')
                cast_str = ', '.join([actor.text for actor in credits_elems[:5]]) if credits_elems else 'No disponible'
                
                # Construir mensaje
                message = (
                    f"🎬 *{title}* ({year})\n\n"
                    f"⭐ *Calificación:* {rating}/10\n"
                    f"🎭 *Género:* {genres}\n"
                    f"👥 *Reparto:* {cast_str}\n\n"
                    f"📝 *Sinopsis:*\n{plot}\n\n"
                    f"🔗 [Ver en IMDb]({imdb_url})"
                )
                
                # Intentar obtener la URL del póster
                poster_elem = soup.select_one('img.ipc-image')
                poster_url = poster_elem['src'] if poster_elem and 'src' in poster_elem.attrs else None
                
            except Exception as scrape_error:
                logger.error(f"Error en web scraping: {scrape_error}")
                await processing_msg.edit_text(
                    f"❌ Error al obtener información de IMDb: {str(e)[:100]}\n\n"
                    f"Por favor, verifica que el enlace sea correcto y que IMDb esté accesible."
                )
                return
        
        # Enviar mensaje y póster si está disponible
        if poster_url:
            try:
                # Descargar imagen del póster
                poster_response = requests.get(poster_url)
                poster_response.raise_for_status()
                
                # Enviar la imagen con la información como pie de foto
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=BytesIO(poster_response.content),
                    caption=message,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Eliminar mensaje de procesamiento
                await processing_msg.delete()
                
            except Exception as img_error:
                logger.error(f"Error enviando imagen: {img_error}")
                # Si falla el envío de la imagen, enviar solo el texto
                await processing_msg.edit_text(
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            # Si no hay póster, enviar solo el texto
            await processing_msg.edit_text(
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
    
    except Exception as e:
        logger.error(f"Error en comando imdb: {e}")
        await processing_msg.edit_text(
            f"❌ Error al procesar la información de IMDb: {str(e)[:100]}"
        )
        
@check_channel_membership
async def down_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga y envía contenido de Picta.cu al chat."""
    if not update.message:
        return
    
    # Verificar si el usuario proporcionó un enlace
    if not context.args:
        await update.message.reply_text(
            "Por favor, proporciona un enlace de Picta.cu.\n"
            "Ejemplo: /down https://www.picta.cu/movie/memoria-caracol-dyw8jwyqfc79fhvc"
        )
        return
    
    # Obtener el enlace
    picta_url = context.args[0]
    
    # Verificar que es un enlace de Picta.cu válido
    if not re.match(r'https?://(www\.)?picta\.cu/(movie|serie)/.+', picta_url):
        await update.message.reply_text(
            "❌ El enlace proporcionado no es un enlace válido de Picta.cu.\n"
            "Debe ser un enlace a una película o serie en Picta."
        )
        return
    
    # Verificar el tipo de usuario para determinar límites y permisos
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)
    
    # Verificar si el usuario tiene búsquedas disponibles
    if not db.increment_daily_usage(user_id):
        # Mostrar mensaje de límite alcanzado y opciones de planes
        keyboard = []
        for plan_id, plan in PLANS_INFO.items():
            if plan_id != 'basic':
                keyboard.append([
                    InlineKeyboardButton(
                        f"{plan['name']} - {plan['price']}",
                        callback_data=f"buy_plan_{plan_id}"
                    )
                ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Has alcanzado tu límite de búsquedas diarias.\n\n"
            "Para continuar descargando, adquiere un plan premium:",
            reply_markup=reply_markup
        )
        return
    
    # Enviar mensaje de procesamiento
    status_message = await update.message.reply_text(
        "🔍 Analizando el enlace de Picta... Por favor espera."
    )
    
    # Mostrar acción "subiendo video" mientras se procesa
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_VIDEO
    )
    
    try:
        # Obtener información del video usando yt-dlp
        await status_message.edit_text("⏳ Obteniendo información del contenido...")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            ydl_opts = {
                'format': 'best',
                'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': False,
                'noplaylist': True,
            }
            
            video_info = None
            video_path = None
            
            try:
                # Extraer información del video
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    video_info = await asyncio.to_thread(ydl.extract_info, picta_url, download=False)
                    
                    if not video_info:
                        await status_message.edit_text(
                            "❌ No se pudo obtener información del contenido. Es posible que no esté disponible."
                        )
                        return
                    
                    # Actualizar mensaje con información del contenido
                    title = video_info.get('title', 'Contenido de Picta')
                    await status_message.edit_text(
                        f"📥 Descargando: *{title}*\n\n"
                        f"⏳ Iniciando descarga... Esto puede tomar varios minutos dependiendo del tamaño.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Descargar el video
                    await asyncio.to_thread(ydl.download, [picta_url])
                    
                    # Encontrar el archivo descargado
                    files = list(Path(temp_dir).glob('*'))
                    if not files:
                        await status_message.edit_text(
                            "❌ Error: No se pudo descargar el archivo."
                        )
                        return
                    
                    video_path = str(files[0])
                    
                    # Verificar el tamaño del archivo
                    file_size = os.path.getsize(video_path) / (1024 * 1024)  # en MB
                    
                    # Informar sobre el progreso
                    await status_message.edit_text(
                        f"📥 Descargando: *{title}*\n\n"
                        f"✅ Descarga completada: {file_size:.1f} MB\n\n"
                        f"⏳ Enviando a Telegram... Por favor espera.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Enviar el archivo al chat
                    with open(video_path, 'rb') as video_file:
                        # Determinar si es una película o serie
                        is_movie = 'movie' in picta_url
                        file_type = "Película" if is_movie else "Serie"
                        
                        # Enviar el video
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=video_file,
                            caption=f"🎬 *{title}*\n\n"
                                   f"📂 *Tipo:* {file_type}\n"
                                   f"🔗 *Fuente:* Picta.cu\n",
                            parse_mode=ParseMode.MARKDOWN,
                            supports_streaming=True,
                            width=video_info.get('width'),
                            height=video_info.get('height'),
                            duration=video_info.get('duration'),
                        )
                        
                        # Eliminar mensaje de estado
                        await status_message.delete()
                    
            except Exception as e:
                logger.error(f"Error en la descarga con yt-dlp: {e}")
                logger.error(traceback.format_exc())
                
                # Intentar método alternativo si yt-dlp falla
                await status_message.edit_text(
                    "⚠️ Error con el método principal. Intentando método alternativo..."
                )
                
                try:
                    # Intento alternativo usando requests y BeautifulSoup
                    response = requests.get(picta_url, headers={'User-Agent': 'Mozilla/5.0'})
                    response.raise_for_status()
                    
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Buscar la URL del video
                    video_src = None
                    video_tags = soup.find_all('video')
                    source_tags = soup.find_all('source')
                    
                    # Primero verificar las etiquetas <source>
                    for source in source_tags:
                        if 'src' in source.attrs:
                            video_src = source['src']
                            break
                    
                    # Si no, verificar las etiquetas <video>
                    if not video_src:
                        for video in video_tags:
                            if 'src' in video.attrs:
                                video_src = video['src']
                                break
                    
                    # Si aún no hay URL, buscar en scripts
                    if not video_src:
                        scripts = soup.find_all('script')
                        for script in scripts:
                            script_text = script.string if script.string else ""
                            # Buscar URLs de video en el JavaScript
                            video_match = re.search(r'["\'](https?://.*?\.mp4)["\']', script_text)
                            if video_match:
                                video_src = video_match.group(1)
                                break
                    
                    if not video_src:
                        await status_message.edit_text(
                            "❌ No se pudo encontrar el video en la página. El sitio puede haber cambiado su estructura."
                        )
                        return
                    
                    # Descargar el video
                    await status_message.edit_text("📥 Descargando el video desde la URL encontrada...")
                    
                    # Determinar el nombre del archivo
                    parsed_url = urlparse(video_src)
                    file_name = os.path.basename(parsed_url.path)
                    if not file_name or not file_name.endswith(('.mp4', '.mkv', '.avi', '.mov')):
                        file_name = "video_picta.mp4"
                    
                    video_path = f"{temp_dir}/{file_name}"
                    
                    # Descargar con requests
                    with requests.get(video_src, stream=True, headers={'User-Agent': 'Mozilla/5.0'}) as r:
                        r.raise_for_status()
                        total_size = int(r.headers.get('content-length', 0))
                        total_size_mb = total_size / (1024 * 1024)
                        
                        # Actualizar mensaje con tamaño total
                        await status_message.edit_text(
                            f"📥 Descargando video...\n\n"
                            f"💾 Tamaño total: {total_size_mb:.1f} MB"
                        )
                        
                        # Descargar en chunks para archivos grandes
                        downloaded = 0
                        last_percent = 0
                        with open(video_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    
                                    # Actualizar progreso cada 10%
                                    percent = int(downloaded * 100 / total_size)
                                    if percent >= last_percent + 10:
                                        last_percent = percent
                                        await status_message.edit_text(
                                            f"📥 Descargando video...\n\n"
                                            f"💾 Tamaño total: {total_size_mb:.1f} MB\n"
                                            f"⏳ Progreso: {percent}%"
                                        )
                    
                    # Obtener título de la página
                    title_tag = soup.find('title')
                    title = title_tag.text if title_tag else "Contenido de Picta"
                    title = title.replace(" - Picta", "").strip()
                    
                    # Informar finalización de descarga
                    await status_message.edit_text(
                        f"📥 Descargando: *{title}*\n\n"
                        f"✅ Descarga completada: {total_size_mb:.1f} MB\n\n"
                        f"⏳ Enviando a Telegram... Por favor espera.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Determinar si es una película o serie
                    is_movie = 'movie' in picta_url
                    file_type = "Película" if is_movie else "Serie"
                    
                    # Enviar el video
                    with open(video_path, 'rb') as video_file:
                        await context.bot.send_video(
                            chat_id=update.effective_chat.id,
                            video=video_file,
                            caption=f"🎬 *{title}*\n\n"
                                   f"📂 *Tipo:* {file_type}\n"
                                   f"🔗 *Fuente:* Picta.cu\n",
                            parse_mode=ParseMode.MARKDOWN,
                            supports_streaming=True
                        )
                        
                        # Eliminar mensaje de estado
                        await status_message.delete()
                
                except Exception as alt_e:
                    logger.error(f"Error en método alternativo: {alt_e}")
                    logger.error(traceback.format_exc())
                    await status_message.edit_text(
                        f"❌ No se pudo descargar el contenido: {str(alt_e)[:100]}\n\n"
                        f"Por favor, verifica que el enlace sea correcto y que el contenido esté disponible."
                    )
    
    except Exception as e:
        logger.error(f"Error general en down_command: {e}")
        logger.error(traceback.format_exc())
        await status_message.edit_text(
            f"❌ Error al procesar la descarga: {str(e)[:100]}\n\n"
            f"Por favor, intenta más tarde o con otro enlace."
        )

@check_channel_membership
async def search_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search for content in the channel based on user query."""
    if not update.message:
        return
        
    user_id = update.effective_user.id
    
    # Get search query from command arguments
    if not context.args:
        await update.message.reply_text(
            "Por favor, proporciona el nombre de la película o serie que deseas buscar.\n"
            "Ejemplo: /search Stranger Things"
        )
        return
    
    query = " ".join(context.args).lower()
    
    # Check user's search limits
    user_data = db.get_user(user_id)
    
    if not user_data:
        await update.message.reply_text(
            "❌ Error: Usuario no registrado. Usa /start para registrarte."
        )
        return
    
    # Check if user can make more searches today
    if not db.increment_daily_usage(user_id):
        # Show purchase plans if limit exceeded
        keyboard = []
        for plan_id, plan in PLANS_INFO.items():
            if plan_id != 'basic':
                keyboard.append([
                    InlineKeyboardButton(
                        f"{plan['name']} - {plan['price']}",
                        callback_data=f"buy_plan_{plan_id}"
                    )
                ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Has alcanzado tu límite de búsquedas diarias.\n\n"
            "Para continuar buscando, adquiere un plan premium:",
            reply_markup=reply_markup
        )
        return
    
    # Show typing action
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    
    # Initialize user preferences if not exist
    if user_id not in user_preferences:
        user_preferences[user_id] = {
            "max_results": 5,
            "show_previews": True,
            "sort_by_date": True
        }
    
    # Get user preferences
    max_results = user_preferences[user_id]["max_results"]
    
    # Check if we have cached results for this query
    cache_key = f"{query}_{user_id}"
    if cache_key in search_cache:
        cache_time, results = search_cache[cache_key]
        # Check if cache is still valid
        if (datetime.now() - cache_time).total_seconds() < CACHE_EXPIRATION:
            # Use cached results
            await send_search_results(update, context, query, results)
            return
    
    # Send initial message
    status_message = await update.message.reply_text(
        f"🔍 Buscando '{query}' en el canal... Por favor espera."
    )
    
    try:
        # Get the latest message ID if we don't have it
        if not last_message_id:
            try:
                latest_id = await get_latest_message_id(context)
            except Exception as e:
                logger.error(f"Error getting latest message ID: {e}")
                await status_message.edit_text(
                    f"❌ Error al buscar en el canal. Por favor, intenta más tarde."
                )
                return
        else:
            latest_id = last_message_id
        
        # We'll search through messages more efficiently
        num_messages = min(latest_id, MAX_SEARCH_MESSAGES)
        
        # Create a list of message IDs to check
        # We'll prioritize recent messages and use a smarter search pattern
        message_ids = []
        
        # First, check the most recent 100 messages
        recent_start = max(1, latest_id - 100)
        message_ids.extend(range(latest_id, recent_start - 1, -1))
        
        # Then, check older messages with a larger step to cover more ground quickly
        if recent_start > 1:
            # Calculate how many more messages we can check
            remaining = MAX_SEARCH_MESSAGES - len(message_ids)
            if remaining > 0:
                # Determine step size based on remaining messages
                step = max(1, (recent_start - 1) // remaining)
                older_ids = list(range(recent_start - 1, 0, -step))[:remaining]
                message_ids.extend(older_ids)
        
        # Keep track of potential matches
        potential_matches = []
        
        # Update status message
        await status_message.edit_text(
            f"🔍 Buscando '{query}'... 0% completado"
        )
        
        # Parse special search filters
        movie_filter = "#película" in query or "#pelicula" in query
        series_filter = "#serie" in query or "#series" in query
        
        # Extract year filter if present
        year_match = re.search(r'\+(\d{4})', query)
        year_filter = int(year_match.group(1)) if year_match else None
        
        # Clean query from filters
        clean_query = query
        if movie_filter:
            clean_query = clean_query.replace("#película", "").replace("#pelicula", "")
        if series_filter:
            clean_query = clean_query.replace("#serie", "").replace("#series", "")
        if year_filter:
            clean_query = re.sub(r'\+\d{4}', "", clean_query)
        
        clean_query = clean_query.strip()
        
        # Search through messages in batches to update progress
        batch_size = 20
        total_batches = (len(message_ids) + batch_size - 1) // batch_size
        
        for batch_index in range(0, len(message_ids), batch_size):
            batch = message_ids[batch_index:batch_index + batch_size]
            
            # Process batch in parallel for speed
            tasks = []
            for msg_id in batch:
                task = asyncio.create_task(get_message_content(context, update.effective_chat.id, msg_id))
                tasks.append((msg_id, task))
            
            # Wait for all tasks to complete
            for msg_id, task in tasks:
                try:
                    message_content = await task
                    
                    if message_content:
                        # Check if the message contains the query
                        full_content = message_content['full_content'].lower()
                        
                        # Apply filters
                        if movie_filter and "#serie" in full_content:
                            continue
                        if series_filter and "#película" in full_content:
                            continue
                        
                        # Apply year filter
                        if year_filter and not re.search(r'\b' + str(year_filter) + r'\b', full_content):
                            continue
                        
                        # Check if message matches the query
                        if clean_query in full_content:
                            # Calculate relevance score
                            relevance = 0
                            
                            # Exact match gets higher score
                            if clean_query == full_content:
                                relevance += 100
                            # Title match gets higher score
                            elif re.search(r'^' + re.escape(clean_query), full_content):
                                relevance += 50
                            # Word boundary match gets higher score
                            elif re.search(r'\b' + re.escape(clean_query) + r'\b', full_content):
                                relevance += 25
                            # Otherwise, just a substring match
                            else:
                                relevance += 10
                                
                            # Media content gets higher score
                            if message_content['has_media']:
                                relevance += 15
                                
                            # Recent messages get higher score
                            recency_score = min(10, (msg_id / latest_id) * 10)
                            relevance += recency_score
                            
                            # Add to potential matches
                            potential_matches.append({
                                'id': msg_id,
                                'preview': message_content['preview'],
                                'has_media': message_content['has_media'],
                                'relevance': relevance
                            })
                            
                            # If we have enough matches, we can stop searching
                            if len(potential_matches) >= max_results * 3:  # Get more than needed to sort by relevance
                                break
                
                except Exception as e:
                    logger.error(f"Error processing message {msg_id}: {e}")
                    continue
            
            # Update progress
            progress = min(100, int((batch_index + len(batch)) / len(message_ids) * 100))
            if progress % 10 == 0:  # Update every 10%
                await status_message.edit_text(
                    f"🔍 Buscando '{query}'... {progress}% completado"
                )
            
            # If we have enough matches, stop searching
            if len(potential_matches) >= max_results * 3:
                break
            
            # Avoid hitting rate limits
            await asyncio.sleep(0.01)
        
        # Sort matches by relevance
        if user_id in user_preferences and user_preferences[user_id]["sort_by_date"]:
            # Sort by message ID (date) if user prefers
            potential_matches.sort(key=lambda x: x['id'], reverse=True)
        else:
            # Sort by relevance score
            potential_matches.sort(key=lambda x: x['relevance'], reverse=True)
        
        # Limit to max results
        potential_matches = potential_matches[:max_results]
        
        # Cache the results
        search_cache[cache_key] = (datetime.now(), potential_matches)
        
        # Send results to user
        await send_search_results(update, context, query, potential_matches, status_message)
    
    except Exception as e:
        logger.error(f"Error searching content: {e}")
        await status_message.edit_text(
            f"❌ Ocurrió un error al buscar: {str(e)[:100]}\n\nPor favor intenta más tarde."
        )

@check_channel_membership

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle direct message searches."""
    # Verificar que update.message no sea None
    if not update.message:
        return
        
    user_id = update.effective_user.id
    query = update.message.text.lower()
    
    # Asignar el texto como argumentos para search_content
    context.args = query.split()
    await search_content(update, context)

async def get_latest_message_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Get the latest message ID from the channel."""
    global last_message_id

    try:
        # Verificar primero si el canal existe y el bot tiene acceso
        try:
            await context.bot.get_chat(chat_id=SEARCH_CHANNEL_ID)
        except Exception as e:
            logger.error(f"Error accessing the search channel: {e}")
            raise

        # Send a temporary message to get the latest message ID
        temp_msg = await context.bot.send_message(chat_id=SEARCH_CHANNEL_ID, text=".")
        latest_id = temp_msg.message_id
        
        # Delete the temporary message
        try:
            await context.bot.delete_message(chat_id=SEARCH_CHANNEL_ID, message_id=latest_id)
        except Exception as e:
            logger.error(f"Error deleting temporary message: {e}")
        
        last_message_id = latest_id
        return latest_id
    except Exception as e:
        logger.error(f"Error getting latest message ID: {e}")
        # Return a default value instead of raising an exception
        return last_message_id or 1

async def get_message_content(context: ContextTypes.DEFAULT_TYPE, user_chat_id: int, msg_id: int) -> dict:
    """Get message content efficiently with caching."""
    # Check if we have this message in cache
    if msg_id in message_cache:
        return message_cache[msg_id]
    
    # Try to get message content
    try:
        # Use getMessages API method if available (faster)
        try:
            message = await context.bot.forward_message(
                chat_id=user_chat_id,
                from_chat_id=SEARCH_CHANNEL_ID,
                message_id=msg_id,
                disable_notification=True
            )
            
            # Extract content
            text = message.text if hasattr(message, 'text') and message.text else ""
            caption = message.caption if hasattr(message, 'caption') and message.caption else ""
            has_media = hasattr(message, 'photo') or hasattr(message, 'video') or hasattr(message, 'document')
            
            # Create preview
            full_content = (text + " " + caption).strip()
            preview = full_content[:50] + "..." if len(full_content) > 50 else full_content
            
            # Delete the forwarded message
            await context.bot.delete_message(
                chat_id=user_chat_id,
                message_id=message.message_id
            )
            
            # Create content object
            message_content = {
                'text': text,
                'caption': caption,
                'has_media': has_media,
                'preview': preview,
                'full_content': full_content
            }
            
            # Cache the content
            message_cache[msg_id] = message_content
            
            return message_content
            
        except Exception as e:
            # If forwarding fails, try copying
            try:
                message = await context.bot.copy_message(
                    chat_id=user_chat_id,
                    from_chat_id=SEARCH_CHANNEL_ID,
                    message_id=msg_id,
                    disable_notification=True
                )
                
                # Extract content
                text = message.text if hasattr(message, 'text') and message.text else ""
                caption = message.caption if hasattr(message, 'caption') and message.caption else ""
                has_media = hasattr(message, 'photo') or hasattr(message, 'video') or hasattr(message, 'document')
                
                # Create preview
                full_content = (text + " " + caption).strip()
                preview = full_content[:50] + "..." if len(full_content) > 50 else full_content
                
                # Delete the copied message
                await context.bot.delete_message(
                    chat_id=user_chat_id,
                    message_id=message.message_id
                )
                
                # Create content object
                message_content = {
                    'text': text,
                    'caption': caption,
                    'has_media': has_media,
                    'preview': preview,
                    'full_content': full_content
                }
                
                # Cache the content
                message_cache[msg_id] = message_content
                
                return message_content
                
            except Exception as inner_e:
                # If both methods fail, return None
                logger.error(f"Error getting content for message {msg_id}: {inner_e}")
                return None
    
    except Exception as e:
        logger.error(f"Error processing message {msg_id}: {e}")
        return None

async def send_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, results: list, status_message=None):
    """Send search results to the user."""
    if not status_message:
        status_message = await update.message.reply_text(
            f"🔍 Procesando resultados para '{query}'..."
        )
    
    if results:
        # Create a message with buttons for each match
        keyboard = []
        for i, match in enumerate(results):
            media_icon = "🎬" if match['has_media'] else "📝"
            keyboard.append([
                InlineKeyboardButton(
                    f"{i+1}. {media_icon} {match['preview']}",
                    callback_data=f"send_{match['id']}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            f"✅ Encontré {len(results)} resultados para '{query}'.\n\n"
            "Selecciona uno para verlo:",
            reply_markup=reply_markup
        )
    else:
        # Content not found, offer to make a request
        keyboard = [
            [
                InlineKeyboardButton("Película 🎞️", callback_data=f"req_movie_{query}"),
                InlineKeyboardButton("Serie 📺", callback_data=f"req_series_{query}")
            ],
            [InlineKeyboardButton("Hacer Pedido 📡", callback_data="make_request")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_message.edit_text(
            f"No se encontraron resultados para '{query}'.\n\n"
            f"Comprueba que escribes el nombre correctamente o utiliza variaciones del mismo. "
            f"Prueba escribiendo el nombre en el idioma oficial o español o solamente pon una palabra clave.\n"
            f"¿Quieres hacer un pedido?\n"
            f"Selecciona el tipo y haz clic en 'Hacer pedido'.",
            reply_markup=reply_markup
        )

async def handle_send_callback(query, context, msg_id):
    """Handle send content callback."""
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    can_forward = user_data and user_data.get('can_forward', False)
    
    try:
        # Show typing action
        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.TYPING
        )
        
        try:
            # Usar siempre copy_message pero con diferentes permisos de protección
            await context.bot.copy_message(
                chat_id=query.message.chat_id,
                from_chat_id=SEARCH_CHANNEL_ID,
                message_id=msg_id,
                protect_content=not can_forward  # False para Plus/Ultra, True para Básico/Pro
            )
            
            # Answer the callback query
            await query.answer("Contenido enviado")
            
            # Add share button for easy sharing (only for users who can forward)
            if can_forward:
                share_url = f"https://t.me/MultimediaTVbot?start=content_{msg_id}"
                keyboard = [
                    [InlineKeyboardButton("Compartir 🔗", url=f"https://t.me/share/url?url={share_url}&text=¡Mira%20este%20contenido%20conmigo!")]
                ]
                share_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="¿Te gustó el contenido? ¡Compártelo con tus amigos!",
                    reply_markup=share_markup
                )
            
            # Update the original message to show which content was selected
            # Get the current keyboard
            keyboard = query.message.reply_markup.inline_keyboard
            
            # Find the button that was clicked and mark it as selected
            new_keyboard = []
            for row in keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == query.data:
                        # Mark this button as selected
                        new_row.append(InlineKeyboardButton(
                            f"✅ {button.text}",
                            callback_data=button.callback_data
                        ))
                    else:
                        new_row.append(button)
                new_keyboard.append(new_row)
            
            # Update the message with the new keyboard
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(new_keyboard)
            )
        except Exception as e:
            logger.error(f"Error sending content: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Error al enviar el contenido: {str(e)[:100]}\n\nEs posible que el canal de búsqueda no esté accesible o que el mensaje ya no exista."
            )
    
    except Exception as e:
        logger.error(f"Error handling send callback: {e}")
        await query.answer(f"Error: {str(e)[:200]}")


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle profile button click with real-time limit information"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        await query.edit_message_text("Error al obtener datos del perfil. Intenta con /start")
        return
    
    # Get user plan details
    plan_type = user_data.get('plan_type', 'basic')
    plan_name = PLANS_INFO.get(plan_type, PLANS_INFO['basic'])['name']
    
    # Calculate expiration date if not on basic plan
    expiration_text = ""
    if plan_type != 'basic':
        if 'plan_expiry' in user_data and user_data['plan_expiry']:
            # Verificar si plan_expiry es una cadena o un objeto datetime
            if isinstance(user_data['plan_expiry'], str):
                try:
                    expiry_date = datetime.strptime(user_data['plan_expiry'], '%Y-%m-%d %H:%M:%S')
                    # Calcular días restantes
                    days_left = (expiry_date - datetime.now()).days
                    expiration_text = f"Expira: {expiry_date.strftime('%d/%m/%Y')} ({days_left} días)\n"
                except ValueError:
                    expiration_text = f"Expira: {user_data['plan_expiry']}\n"
            else:
                days_left = (user_data['plan_expiry'] - datetime.now()).days
                expiration_text = f"Expira: {user_data['plan_expiry'].strftime('%d/%m/%Y')} ({days_left} días)\n"
    
    # Get search and request limits based on plan
    plan_info = PLANS_INFO.get(plan_type, PLANS_INFO['basic'])
    content_limit = plan_info['searches_per_day']
    request_limit = plan_info['requests_per_day']
    
    # Get current usage
    current_searches = user_data.get('daily_searches', 0)
    current_requests = user_data.get('daily_requests', 0)
    
    # Calculate remaining searches and requests
    if content_limit == float('inf'):
        searches_remaining_text = "Ilimitado"
    else:
        searches_remaining = max(0, content_limit - current_searches)
        searches_remaining_text = f"{searches_remaining}/{content_limit}"
    
    if request_limit == float('inf'):
        requests_remaining_text = "Ilimitado"
    else:
        requests_remaining = max(0, request_limit - current_requests)
        requests_remaining_text = f"{requests_remaining}/{request_limit}"
    
    # Calculate next reset time (midnight)
    now = datetime.now()
    next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    time_until_reset = next_reset - now
    hours, remainder = divmod(time_until_reset.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    reset_text = f"{hours:02d}:{minutes:02d}"
    
    # Get referral count
    referral_count = db.get_referral_count(user_id)
    
    # Format join date
    join_date = user_data.get('join_date', now.strftime('%Y-%m-%d %H:%M:%S'))
    if isinstance(join_date, str):
        try:
            join_date = datetime.strptime(join_date, '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y')
        except ValueError:
            join_date = now.strftime('%d/%m/%Y')
    elif isinstance(join_date, datetime):
        join_date = join_date.strftime('%d/%m/%Y')
    else:
        join_date = now.strftime('%d/%m/%Y')
    
    # Create profile message with real-time limits
    profile_text = (
        f"👤 *Perfil de Usuario*\n\n"
        f"Nombre: {query.from_user.first_name}\n"
        f"Saldo: {user_data.get('balance', 0)} 💎\n"
        f"ID: {user_id}\n"
        f"Plan: {plan_name}\n"
        f"{expiration_text}"
        f"Pedidos restantes: {requests_remaining_text}\n"
        f"Búsquedas restantes: {searches_remaining_text}\n"
        f"Fecha de Unión: {join_date}\n"
        f"Referidos: {referral_count}\n"
        f"Reinicio en: {reset_text}\n\n"
        f"🎁 Comparte tu enlace de referido y gana diamantes!"
    )
    
    # Create buttons
    keyboard = [
        [InlineKeyboardButton("Compartir Enlace de referencia 🔗", 
                             url=f"https://t.me/share/url?url=https://t.me/MultimediaTVbot?start=ref_{user_id}&text=¡Únete%20y%20ve%20películas%20conmigo!")],
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=profile_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plans button click"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        await query.edit_message_text("Error al obtener datos del usuario. Intenta con /start")
        return
    
    # Get user plan details
    plan_type = user_data.get('plan_type', 'basic')
    plan_name = PLANS_INFO.get(plan_type, PLANS_INFO['basic'])['name']
    
    # Create plans message
    plans_text = (
        f"▧ Planes de Suscripción ▧\n\n"
        f"Tu saldo actual: {user_data.get('balance', 0)} 💎\n"
        f"Plan actual: {plan_name}\n\n"
        f"📋 Planes Disponibles:\n\n"
        f"Pro (169.99 | 29 ⭐)\n"
        f"169.99 CUP\n"
        f"0.49 USD\n\n"
        f"Plus (649.99 | 117 ⭐)\n"
        f"649.99 CUP\n"
        f"1.99 USD\n\n"
        f"Ultra (1049.99 | 176 ⭐)\n"
        f"1049.99 CUP\n"
        f"2.99 USD\n\n"
        f"Pulsa los botones de debajo para mas info de los planes y formas de pago."
    )
    
    # Create buttons
    keyboard = [
        [
            InlineKeyboardButton("Plan pro ✨", callback_data="plan_pro"),
            InlineKeyboardButton("Plan plus ⭐", callback_data="plan_plus"),
            InlineKeyboardButton("Plan ultra 🌟", callback_data="plan_ultra")
        ],
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=plans_text,
        reply_markup=reply_markup
    )

async def handle_plan_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plan details button clicks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    callback_data = query.data
    
    if not user_data:
        await query.edit_message_text("Error al obtener datos del usuario. Intenta con /start")
        return
    
    plan_details = ""
    if callback_data == "plan_pro":
        plan_details = (
            f"💫 Plan Pro - Detalles 💫\n\n"
            f"Precio: 169.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ 2 pedidos diarios\n"
            f"└ 15 películas o series al día\n"
            f"└ No puede reenviar contenido ni guardarlo\n\n"
            f"Tu saldo actual: {user_data.get('balance', 0)} 💎"
        )
    elif callback_data == "plan_plus":
        plan_details = (
            f"💫 Plan Plus - Detalles 💫\n\n"
            f"Precio: 649.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ 10 pedidos diarios\n"
            f"└ 50 películas o series al día\n"
            f"└ Soporte prioritario\n"
            f"└ Enlaces directos de descarga\n"
            f"└ Acceso a contenido exclusivo\n\n"
            f"Tu saldo actual: {user_data.get('balance', 0)} 💎"
        )
    elif callback_data == "plan_ultra":
        plan_details = (
            f"⭐ Plan Ultra - Detalles ⭐\n\n"
            f"Precio: 1049.99\n"
            f"Duración: 30 días\n\n"
            f"Beneficios:\n"
            f"└ Pedidos ilimitados\n"
            f"└ Sin restricciones de contenido\n"
            f"└ Reenvío y guardado permitido\n"
            f"└ Enlaces directos de descarga\n"
            f"└ Soporte VIP\n"
            f"└ Acceso anticipado a nuevo contenido\n\n"
            f"Tu saldo actual: {user_data.get('balance', 0)} 💎"
        )
    
    # Create buttons
    keyboard = [
        [
            InlineKeyboardButton("Cup (Cuba 🇨🇺)", callback_data=f"{callback_data}_cup"),
            InlineKeyboardButton("Crypto", callback_data=f"{callback_data}_crypto")
        ],
        [InlineKeyboardButton("Volver 🔙", callback_data="plans")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=plan_details,
        reply_markup=reply_markup
    )

async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment method selection"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    plan_type, payment_method = callback_data.rsplit('_', 1)
    
    payment_info = ""
    if payment_method == "cup":
        if plan_type == "plan_pro":
            payment_info = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 169.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 189.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_type == "plan_plus":
            payment_info = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 649.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 669.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_type == "plan_ultra":
            payment_info = (
                f"Pago en CUP (Transferencia)\n"
                f"Precio: 1049.99 CUP\n"
                f"Pago en CUP (Saldo)\n"
                f"Precio: 1089.99 CUP\n"
                f"Detalles de pago:\n"
                f"Número: 9205 1299 7736 4067\n"
                f"Telef: 55068190\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
    elif payment_method == "crypto":
        if plan_type == "plan_pro":
            payment_info = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 0.49 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_type == "plan_plus":
            payment_info = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 1.99 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
        elif plan_type == "plan_ultra":
            payment_info = (
                f"Pago con USDT (BEP 20)\n"
                f"Precio: 2.99 USDTT\n"
                f"Detalles de pago:\n"
                f"Dirección: 0x26d89897c4e452C7BD3a0B8Aa79dD84E516BD4c6\n\n"
                f"⚠️ Después de realizar el pago, mandar captura del pago a @osvaldo20032 para activar tu plan."
            )
    
    # Create back button
    keyboard = [
        [InlineKeyboardButton("Volver 🔙", callback_data=plan_type)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=payment_info,
        reply_markup=reply_markup
    )

async def handle_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle info button click"""
    query = update.callback_query
    await query.answer()
    
    info_text = (
        "Funcionamiento del bot:\n\n"
        "Comandos:\n"
        "/start - Inicia el bot y envía el mensaje de bienvenida con los botones principales\n"
        "/search - Seguido del nombre de la película o serie, buscará en el canal y luego enviará al usuario\n\n"
        "Si la película o serie no se encuentra en el canal, el bot te permitirá hacer un pedido.\n\n"
        "Búsquedas para usuarios sin plan premium: solo podrán realizar 3 búsquedas diarias, 1 pedido diario y no se les permitirá reenviar el video."
    )
    
    # Create back button
    keyboard = [
        [InlineKeyboardButton("Volver 🔙", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=info_text,
        reply_markup=reply_markup
    )

async def handle_request_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle request type selection"""
    query = update.callback_query
    await query.answer()
    
    # Store the request type in user_data
    context.user_data['request_type'] = query.data
    
    await query.edit_message_text(
        text="Tipo seleccionado. Ahora haz clic en 'Hacer Pedido 📡' para enviar tu solicitud.",
        reply_markup=update.callback_query.message.reply_markup
    )

async def handle_make_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle make request button click"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        await query.edit_message_text("Error al obtener datos del usuario. Intenta con /start")
        return
    
    # Check if user has requests left
    requests_left = db.get_requests_left(user_id)
    if requests_left <= 0:
        await query.edit_message_text(
            "Has alcanzado el límite de pedidos diarios para tu plan.\n"
            "Considera actualizar tu plan para obtener más pedidos."
        )
        return
    
    # Get request type and content name
    callback_data = context.user_data.get('request_type', '')
    if not callback_data:
        await query.edit_message_text(
            "Por favor, selecciona primero el tipo de contenido (Película o Serie)."
        )
        return
    
    try:
        req_type, content_name = callback_data.split('_', 2)[1:]
    except ValueError:
        await query.edit_message_text(
            "Error al procesar la solicitud. Por favor, intenta nuevamente."
        )
        return
    
    # Update user's request count
    db.update_request_count(user_id)
    
    # Send request to admin
    try:
        keyboard = [
            [InlineKeyboardButton("Aceptar ✅", callback_data=f"accept_req_{user_id}_{content_name}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📩 *Nuevo Pedido*\n\n"
                 f"Usuario: {query.from_user.first_name} (@{query.from_user.username})\n"
                 f"ID: {user_id}\n"
                 f"Tipo: {'Película' if req_type == 'movie' else 'Serie'}\n"
                 f"Nombre: {content_name}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Confirm to user
        await query.edit_message_text(
            f"✅ Tu pedido de {'película' if req_type == 'movie' else 'serie'} '{content_name}' ha sido enviado al administrador.\n"
            f"Te notificaremos cuando esté disponible.\n"
            f"Te quedan {requests_left-1} pedidos hoy."
        )
    except Exception as e:
        logger.error(f"Error sending request to admin: {e}")
        await query.edit_message_text(
            "Error al enviar el pedido. Intenta más tarde."
        )

async def handle_accept_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin accepting a content request"""
    query = update.callback_query
    await query.answer()
    
    # Check if user is admin
    if query.from_user.id != ADMIN_ID:
        return
    
    # Parse callback data
    try:
        _, req_type, user_id, content_name = query.data.split('_', 3)
        user_id = int(user_id)
        
        # Notify user that request was accepted
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ ¡Buenas noticias! Tu solicitud para '{content_name}' ha sido aceptada.\n"
                 f"El contenido estará disponible pronto en el bot. Podrás buscarlo usando /search."
        )
        
        # Update admin's message
        await query.edit_message_text(
            text=f"✅ Pedido aceptado: {content_name}\n"
                 f"El usuario ha sido notificado.",
            reply_markup=None
        )
    except Exception as e:
        logger.error(f"Error handling accept request: {e}")
        await query.edit_message_text(
            text="Error al procesar la aceptación del pedido.",
            reply_markup=None
        )

async def set_user_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to set a user's plan"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    # Check arguments
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uso: /plan @username número_de_plan\n"
            "1 - Plan Pro\n"
            "2 - Plan Plus\n"
            "3 - Plan Ultra"
        )
        return
    
    username = context.args[0].replace('@', '')
    try:
        plan_number = int(context.args[1])
        if plan_number not in [1, 2, 3]:
            raise ValueError("Número de plan inválido")
        
        plan_map = {1: 'pro', 2: 'plus', 3: 'ultra'}
        plan_type = plan_map[plan_number]
        
        # Get user_id from username
        user_id = db.get_user_id_by_username(username)
        if not user_id:
            await update.message.reply_text(f"Usuario @{username} no encontrado en la base de datos.")
            return
        
        # Update user's plan
        expiry_date = datetime.now() + timedelta(days=30)
        db.update_plan(user_id, plan_type, expiry_date)
        
        # Notify user about plan change
        plan_name = PLANS_INFO[plan_type]['name']
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 ¡Felicidades! Tu plan ha sido actualizado a {plan_name}.\n"
                     f"Expira el: {expiry_date.strftime('%d/%m/%Y')}\n"
                     f"Disfruta de todos los beneficios de tu nuevo plan."
            )
        except Exception as e:
            logger.error(f"Error notifying user about plan change: {e}")
        
        await update.message.reply_text(
            f"Plan de @{username} actualizado a {plan_name}.\n"
            f"Expira el: {expiry_date.strftime('%d/%m/%Y')}"
        )
    except ValueError:
        await update.message.reply_text(
            "Número de plan inválido. Debe ser 1, 2 o 3."
        )
    except Exception as e:
        logger.error(f"Error setting user plan: {e}")
        await update.message.reply_text(
            "Error al actualizar el plan del usuario."
        )

async def add_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to create a gift code"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    # Check arguments
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uso: /addgift_code código plan_number max_uses\n"
            "Ejemplo: /addgift_code 2432 3 1\n"
            "1 - Plan Pro\n"
            "2 - Plan Plus\n"
            "3 - Plan Ultra"
        )
        return
    
    try:
        code = context.args[0]
        plan_number = int(context.args[1])
        max_uses = int(context.args[2])
        
        if plan_number not in [1, 2, 3]:
            raise ValueError("Número de plan inválido")
        
        plan_map = {1: 'pro', 2: 'plus', 3: 'ultra'}
        plan_type = plan_map[plan_number]
        
        # Add gift code to database
        db.add_gift_code(code, plan_type, max_uses)
        
        await update.message.reply_text(
            f"Código de regalo '{code}' creado para el plan {PLANS_INFO[plan_type]['name']}.\n"
            f"Usos máximos: {max_uses}"
        )
    except ValueError:
        await update.message.reply_text(
            "Formato inválido. Usa /addgift_code código plan_number max_uses"
        )
    except Exception as e:
        logger.error(f"Error adding gift code: {e}")
        await update.message.reply_text(
            "Error al crear el código de regalo."
        )

async def redeem_gift_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to redeem a gift code"""
    user_id = update.effective_user.id
    
    # Check arguments
    if not context.args:
        await update.message.reply_text(
            "Uso: /gift_code código\n"
            "Ejemplo: /gift_code 2432"
        )
        return
    
    code = context.args[0]
    
    try:
        # Check if code exists and is valid
        gift_code_data = db.get_gift_code(code)
        if not gift_code_data:
            await update.message.reply_text(
                "Código de regalo inválido o ya ha sido utilizado."
            )
            return
        
        # Update user's plan
        plan_type = gift_code_data['plan_type']
        expiry_date = datetime.now() + timedelta(days=30)
        db.update_plan(user_id, plan_type, expiry_date)
        
        # Update gift code usage
        db.update_gift_code_usage(code)
        
        # Notify user
        plan_name = PLANS_INFO[plan_type]['name']
        await update.message.reply_text(
            f"🎉 ¡Felicidades! Has canjeado un código de regalo.\n"
            f"Tu plan ha sido actualizado a {plan_name}.\n"
            f"Expira el: {expiry_date.strftime('%d/%m/%Y')}\n"
            f"Disfruta de todos los beneficios de tu nuevo plan."
        )
    except Exception as e:
        logger.error(f"Error redeeming gift code: {e}")
        await update.message.reply_text(
            "Error al canjear el código de regalo. Intenta más tarde."
        )

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to ban a user"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    # Check arguments
    if not context.args:
        await update.message.reply_text(
            "Uso: /ban @username o /ban user_id"
        )
        return
    
    target = context.args[0]
    
    try:
        # Check if target is username or user_id
        if target.startswith('@'):
            username = target.replace('@', '')
            user_id = db.get_user_id_by_username(username)
            if not user_id:
                await update.message.reply_text(f"Usuario {target} no encontrado.")
                return
        else:
            try:
                user_id = int(target)
                if not db.user_exists(user_id):
                    await update.message.reply_text(f"Usuario con ID {user_id} no encontrado.")
                    return
            except ValueError:
                await update.message.reply_text("Formato inválido. Usa /ban @username o /ban user_id")
                return
        
        # Ban user
        db.ban_user(user_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⛔ Has sido baneado del bot MultimediaTv. Si crees que es un error, contacta al administrador."
            )
        except Exception as e:
            logger.error(f"Error notifying banned user: {e}")
        
        await update.message.reply_text(f"Usuario con ID {user_id} ha sido baneado.")
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        await update.message.reply_text(
            "Error al banear al usuario."
        )

async def upload_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to upload content to both search and main channels"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    # Check if message is a reply to a media message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Este comando debe ser usado respondiendo a un mensaje que contenga "
            "la película, serie o imagen con su descripción."
        )
        return
    
    original_message = update.message.reply_to_message
    status_message = await update.message.reply_text("Procesando subida a múltiples canales...")
    
    try:
        # Resultados para informar al admin
        results = []
        content_ids = []
        
        # 1. Verificar acceso a los canales
        try:
            await context.bot.get_chat(chat_id=SEARCH_CHANNEL_ID)
            await context.bot.get_chat(chat_id=CHANNEL_ID)
        except Exception as e:
            await status_message.edit_text(
                f"❌ Error al acceder a los canales. Verifica que el bot sea administrador de ambos canales.\n"
                f"Error: {str(e)}"
            )
            return
        
        # 2. Enviar al canal de búsqueda (SEARCH_CHANNEL_ID)
        try:
            search_msg = await context.bot.copy_message(
                chat_id=SEARCH_CHANNEL_ID,
                from_chat_id=update.effective_chat.id,
                message_id=original_message.message_id
            )
            
            content_ids.append(search_msg.message_id)
            results.append(f"✅ Canal de búsqueda: Enviado (ID: #{search_msg.message_id})")
            
            # Generar URL y botón para el canal de búsqueda
            share_url = f"https://t.me/MultimediaTVbot?start=content_{search_msg.message_id}"
            keyboard = [
                [InlineKeyboardButton("Ver", url=share_url)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Añadir botón al mensaje en el canal de búsqueda
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=SEARCH_CHANNEL_ID,
                    message_id=search_msg.message_id,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Error añadiendo botón al canal de búsqueda: {e}")
                results.append(f"⚠️ Canal de búsqueda: Enviado pero sin botón 'Ver'")
        
        except Exception as e:
            logger.error(f"Error enviando al canal de búsqueda: {e}")
            results.append(f"❌ Canal de búsqueda: Error al enviar - {str(e)[:50]}")
        
        # 3. Enviar al canal principal (CHANNEL_ID)
        try:
            channel_msg = await context.bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=update.effective_chat.id,
                message_id=original_message.message_id
            )
            
            content_ids.append(channel_msg.message_id)
            results.append(f"✅ Canal principal: Enviado (ID: #{channel_msg.message_id})")
            
            # Usar el mismo botón para el canal principal si se envió correctamente al canal de búsqueda
            if len(content_ids) > 0:
                share_url = f"https://t.me/MultimediaTVbot?start=content_{content_ids[0]}"
                keyboard = [
                    [InlineKeyboardButton("Ver", url=share_url)]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Añadir botón al mensaje en el canal principal
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=CHANNEL_ID,
                        message_id=channel_msg.message_id,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Error añadiendo botón al canal principal: {e}")
                    results.append(f"⚠️ Canal principal: Enviado pero sin botón 'Ver'")
            
        except Exception as e:
            logger.error(f"Error enviando al canal principal: {e}")
            results.append(f"❌ Canal principal: Error al enviar - {str(e)[:50]}")
        
        # 4. Informar al administrador del resultado
        result_text = "📤 Resultado de la subida:\n\n" + "\n".join(results)
        
        if len(content_ids) > 0:
            result_text += "\n\n✅ Contenido subido a " + str(len(content_ids)) + " canal(es)"
            if len(content_ids) == 2:
                result_text += "\n\nEl botón 'Ver' utilizará el ID del canal de búsqueda en ambos mensajes."
        else:
            result_text += "\n\n❌ Error: No se pudo subir el contenido a ningún canal"
        
        await status_message.edit_text(result_text)
        
    except Exception as e:
        logger.error(f"Error general en upload_content: {e}")
        await status_message.edit_text(
            f"❌ Error general al procesar la subida: {str(e)}\nIntenta más tarde."
        )

async def request_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to request a specific movie or series"""
    user_id = update.effective_user.id
    
    # Check arguments
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uso: /pedido año nombre_del_contenido\n"
            "Ejemplo: /pedido 2023 Oppenheimer"
        )
        return
    
    # Check if user is banned
    if db.is_user_banned(user_id):
        await update.message.reply_text(
            "No puedes realizar pedidos porque has sido baneado del bot."
        )
        return
    
    # Check if user has requests left
    requests_left = db.get_requests_left(user_id)
    if requests_left <= 0:
        await update.message.reply_text(
            "Has alcanzado el límite de pedidos diarios para tu plan.\n"
            "Considera actualizar tu plan para obtener más pedidos."
        )
        return
    
    year = context.args[0]
    content_name = " ".join(context.args[1:])
    
    # Update user's request count
    db.update_request_count(user_id)
    
    # Send request to admin
    try:
        keyboard = [
            [InlineKeyboardButton("Aceptar ✅", callback_data=f"accept_req_{user_id}_{content_name}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📩 *Nuevo Pedido*\n\n"
                 f"Usuario: {update.effective_user.first_name} (@{update.effective_user.username})\n"
                 f"ID: {user_id}\n"
                 f"Año: {year}\n"
                 f"Nombre: {content_name}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Confirm to user
        await update.message.reply_text(
            f"✅ Tu pedido '{content_name}' ({year}) ha sido enviado al administrador.\n"
            f"Te notificaremos cuando esté disponible.\n"
            f"Te quedan {requests_left-1} pedidos hoy."
        )
    except Exception as e:
        logger.error(f"Error sending request to admin: {e}")
        await update.message.reply_text(
            "Error al enviar el pedido. Intenta más tarde."
        )

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to show all available commands"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    help_text = (
        "📋 Comandos de Administrador 📋\n\n"
        "Gestión de Usuarios:\n"
        "/plan @username número_plan - Asigna un plan a un usuario\n"
        "   1 - Plan Pro\n"
        "   2 - Plan Plus\n"
        "   3 - Plan Ultra\n\n"
        "/ban @username - Banea a un usuario\n\n"
        "Gestión de Contenido:\n"
        "/up - Responde a un mensaje con este comando para subirlo al canal\n\n"
        "Códigos de Regalo:\n"
        "/addgift_code código plan_number max_uses - Crea un código de regalo\n"
        "   Ejemplo: /addgift_code 2432 3 1\n\n"
        "Estadísticas:\n"
        "/stats - Muestra estadísticas del bot\n\n"
        "Comunicación:\n"
        "/broadcast mensaje - Envía un mensaje a todos los usuarios"
    )
    
    await update.message.reply_text(text=help_text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to show bot statistics"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    try:
        total_users = db.get_total_users()
        active_users = db.get_active_users()
        premium_users = db.get_premium_users()
        total_searches = db.get_total_searches()
        total_requests = db.get_total_requests()
        
        stats_text = (
            "📊 Estadísticas del Bot 📊\n\n"
            f"👥 Usuarios:\n"
            f"- Total: {total_users}\n"
            f"- Activos (últimos 7 días): {active_users}\n"
            f"- Con plan premium: {premium_users}\n\n"
            f"🔍 Actividad:\n"
            f"- Búsquedas totales: {total_searches}\n"
            f"- Pedidos totales: {total_requests}\n\n"
            f"📈 Distribución de Planes:\n"
            f"- Básico: {db.get_users_by_plan('basic')}\n"
            f"- Pro: {db.get_users_by_plan('pro')}\n"
            f"- Plus: {db.get_users_by_plan('plus')}\n"
            f"- Ultra: {db.get_users_by_plan('ultra')}"
        )
        
        await update.message.reply_text(text=stats_text)
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await update.message.reply_text(
            "Error al obtener estadísticas."
        )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to broadcast a message to all users"""
    user = update.effective_user
    
    # Check if user is admin
    if user.id != ADMIN_ID:
        return
    
    # Check arguments
    if not context.args:
        await update.message.reply_text(
            "Uso: /broadcast mensaje"
        )
        return
    
    message = " ".join(context.args)
    
    # Get all user IDs
    user_ids = db.get_all_user_ids()
    
    sent_count = 0
    failed_count = 0
    
    await update.message.reply_text(
        f"Iniciando difusión a {len(user_ids)} usuarios..."
    )
    
    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 *Anuncio Oficial*\n\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent_count += 1
            
            # Add a small delay to avoid hitting rate limits
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Error sending broadcast to {user_id}: {e}")
            failed_count += 1
    
    await update.message.reply_text(
        f"Difusión completada:\n"
        f"✅ Enviados: {sent_count}\n"
        f"❌ Fallidos: {failed_count}"
    )
    
async def upser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando para administradores para iniciar/finalizar la carga de series"""
    user = update.effective_user
    
    # Verificar que el usuario es administrador
    if user.id != ADMIN_ID:
        return
    
    # Obtener el estado actual
    upser_state = context.user_data.get('upser_state', UPSER_STATE_IDLE)
    
    # Si estamos en estado IDLE, iniciar el proceso
    if upser_state == UPSER_STATE_IDLE:
        # Inicializar o reiniciar la estructura de datos para la serie
        context.user_data['upser_episodes'] = []
        context.user_data['upser_state'] = UPSER_STATE_RECEIVING
        context.user_data['upser_cover'] = None
        context.user_data['upser_description'] = None
        
        await update.message.reply_text(
            "📺 *Modo de carga de series activado*\n\n"
            "1️⃣ Envía los capítulos en orden uno por uno\n"
            "2️⃣ Luego envía una imagen con la descripción de la serie\n"
            "3️⃣ Finalmente, envía /upser nuevamente para finalizar y subir la serie\n\n"
            "Para cancelar el proceso, envía /cancelupser",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Si estamos recibiendo capítulos y tenemos una portada, finalizamos
    elif upser_state == UPSER_STATE_COVER and context.user_data.get('upser_cover'):
        await finalize_series_upload(update, context)
    
    # Si estamos recibiendo capítulos pero no tenemos portada, indicar que falta
    elif upser_state == UPSER_STATE_RECEIVING:
        await update.message.reply_text(
            "⚠️ Aún no has enviado la imagen con la descripción de la serie.\n\n"
            "Por favor, envía primero la imagen antes de finalizar el proceso.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Cualquier otro estado (no debería ocurrir)
    else:
        await update.message.reply_text(
            "❌ Error en el estado de carga de series. Reinicia el proceso con /upser."
        )
        context.user_data['upser_state'] = UPSER_STATE_IDLE

async def cancel_upser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancelar el proceso de carga de series"""
    user = update.effective_user
    
    # Verificar que el usuario es administrador
    if user.id != ADMIN_ID:
        return
    
    # Reiniciar el estado
    context.user_data['upser_state'] = UPSER_STATE_IDLE
    context.user_data['upser_episodes'] = []
    context.user_data['upser_cover'] = None
    context.user_data['upser_description'] = None
    
    await update.message.reply_text(
        "❌ Proceso de carga de series cancelado.\n\n"
        "Todos los datos temporales han sido eliminados."
    )

async def handle_upser_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejar la recepción de capítulos y portada durante el proceso de carga de series"""
    user = update.effective_user
    
    # Verificar que el usuario es administrador
    if user.id != ADMIN_ID:
        return
    
    # Verificar si estamos en modo de carga de series
    upser_state = context.user_data.get('upser_state', UPSER_STATE_IDLE)
    if upser_state == UPSER_STATE_IDLE:
        return  # No estamos en modo de carga de series
    
    # Si recibimos un mensaje con foto y estamos en modo de recepción, es la portada
    if update.message.photo and upser_state == UPSER_STATE_RECEIVING:
        # Guardar la portada y descripción
        context.user_data['upser_cover'] = update.message.photo[-1].file_id
        context.user_data['upser_description'] = update.message.caption or "Sin descripción"
        context.user_data['upser_state'] = UPSER_STATE_COVER
        
        await update.message.reply_text(
            "✅ Portada recibida correctamente.\n\n"
            "Ahora envía /upser nuevamente para finalizar y subir la serie.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Si estamos en modo de recepción y recibimos un video/documento, es un capítulo
    if (update.message.video or update.message.document) and upser_state == UPSER_STATE_RECEIVING:
        # Determinar el número de capítulo
        episode_number = len(context.user_data.get('upser_episodes', [])) + 1
        
        # Guardar el capítulo
        episode_data = {
            'message_id': update.message.message_id,
            'episode_number': episode_number,
            'chat_id': update.effective_chat.id,
        }
        
        context.user_data.setdefault('upser_episodes', []).append(episode_data)
        
        await update.message.reply_text(
            f"✅ Capítulo {episode_number} recibido y guardado.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

async def finalize_series_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Finalizar el proceso de carga y subir la serie a los canales"""
    episodes = context.user_data.get('upser_episodes', [])
    cover_photo = context.user_data.get('upser_cover')
    description = context.user_data.get('upser_description', "Sin descripción")
    
    # Verificar que tenemos todos los datos necesarios
    if not episodes or not cover_photo:
        await update.message.reply_text(
            "❌ No hay suficientes datos para subir la serie.\n\n"
            "Debes enviar al menos un capítulo y una imagen de portada."
        )
        return
    
    # Mensaje de estado para seguir el progreso
    status_message = await update.message.reply_text(
        "⏳ Procesando la serie y subiendo a los canales...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        # 1. Subir la portada con descripción al canal de búsqueda
        sent_cover = await context.bot.send_photo(
            chat_id=SEARCH_CHANNEL_ID,
            photo=cover_photo,
            caption=description
        )
        
        search_channel_cover_id = sent_cover.message_id
        
        # 2. Subir todos los capítulos al canal de búsqueda (silenciosamente)
        search_channel_episode_ids = []
        
        for episode in episodes:
            # Obtener el mensaje original
            original_message = await context.bot.copy_message(
                chat_id=SEARCH_CHANNEL_ID,
                from_chat_id=episode['chat_id'],
                message_id=episode['message_id'],
                disable_notification=True
            )
            
            search_channel_episode_ids.append(original_message.message_id)
        
        # 3. Crear un identificador único para esta serie
        series_id = int(time.time())
        
        # 4. Generar URL para el botón "Ver ahora"
        view_url = f"https://t.me/MultimediaTVbot?start=series_{series_id}"
        
        # 5. Crear un botón para la portada
        keyboard = [
            [InlineKeyboardButton("Ver ahora", url=view_url)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # 6. Actualizar la portada en el canal de búsqueda con el botón
        await context.bot.edit_message_reply_markup(
            chat_id=SEARCH_CHANNEL_ID,
            message_id=search_channel_cover_id,
            reply_markup=reply_markup
        )
        
        # 7. Repetir el proceso para el canal principal
        sent_cover_main = await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=cover_photo,
            caption=description
        )
        
        # 8. Actualizar la portada en el canal principal con el mismo botón
        await context.bot.edit_message_reply_markup(
            chat_id=CHANNEL_ID,
            message_id=sent_cover_main.message_id,
            reply_markup=reply_markup
        )
        
        # 9. Guardar los datos en la base de datos
        # Extraer título de la descripción (primera línea o primeros 50 caracteres)
        title = description.split('\n')[0] if '\n' in description else description[:50]
        
        # Guardar la serie en la base de datos
        db.add_series(
            series_id=series_id,
            title=title,
            description=description,
            cover_message_id=search_channel_cover_id,
            added_by=update.effective_user.id
        )
        
        # Guardar los capítulos en la base de datos
        for i, episode_id in enumerate(search_channel_episode_ids):
            db.add_episode(
                series_id=series_id,
                episode_number=i + 1,
                message_id=episode_id
            )
        
        # 10. Reiniciar el estado
        context.user_data['upser_state'] = UPSER_STATE_IDLE
        context.user_data['upser_episodes'] = []
        context.user_data['upser_cover'] = None
        context.user_data['upser_description'] = None
        
        # 11. Informar al administrador
        await status_message.edit_text(
            f"✅ Serie subida correctamente a los canales.\n\n"
            f"📊 Detalles:\n"
            f"- Capítulos: {len(episodes)}\n"
            f"- ID de serie: {series_id}\n"
            f"- Canal de búsqueda: ✓\n"
            f"- Canal principal: ✓\n\n"
            f"Los usuarios pueden acceder a la serie con el botón 'Ver ahora'."
        )
        
    except Exception as e:
        logger.error(f"Error subiendo serie: {e}")
        await status_message.edit_text(
            f"❌ Error al subir la serie: {str(e)[:100]}\n\n"
            f"Por favor, intenta nuevamente."
        )

async def verify_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica la membresía del usuario cuando presiona el botón 'Ya me uní'."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Verificar membresía en tiempo real
    is_member = await is_channel_member(user_id, context)
    
    if is_member:
        # Actualizar caché de verificación
        verification_cache = context.bot_data.setdefault('verification_cache', {})
        verification_cache[user_id] = datetime.now() + timedelta(minutes=30)
        
        # Mostrar mensaje de éxito y redirigir al menú principal
        keyboard = [
            [
                InlineKeyboardButton("Multimedia Tv 📺", url=f"https://t.me/multimediatvOficial"),
                InlineKeyboardButton("Pedidos 📡", url=f"https://t.me/+X9S4pxF8c7plYjYx")
            ],
            [InlineKeyboardButton("Perfil 👤", callback_data="profile")],
            [InlineKeyboardButton("Planes 📜", callback_data="plans")],
            [InlineKeyboardButton("Información 📰", callback_data="info")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ ¡Verificación exitosa! Gracias por unirte a nuestro canal.\n\n"
            f"Ya puedes disfrutar de todas las funcionalidades del bot.",
            reply_markup=reply_markup
        )
    else:
        # Mostrar mensaje de error si aún no se ha unido
        keyboard = [
            [InlineKeyboardButton("Unirse al Canal 📢", url=f"https://t.me/multimediatvOficial")],
            [InlineKeyboardButton("Verificar nuevamente 🔄", callback_data="verify_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "❌ No se ha detectado tu membresía en el canal.\n\n"
            "Por favor, asegúrate de:\n"
            "1. Hacer clic en 'Unirse al Canal 📢'\n"
            "2. Aceptar unirte al canal\n"
            "3. Volver aquí y presionar 'Verificar nuevamente 🔄'",
            reply_markup=reply_markup
        )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline buttons"""
    query = update.callback_query
    data = query.data
    
    # Manejar callback de verificación de membresía
    if data == "verify_membership":
        await verify_channel_membership(update, context)
        return
    
    # Manejar solicitudes de capítulos individuales
    if data.startswith("ep_") and not data.startswith("ep_all_"):
        try:
            # Formato: ep_[series_id]_[episode_number]
            _, series_id, episode_number = data.split("_")
            series_id = int(series_id)
            episode_number = int(episode_number)
            
            await send_episode(query, context, series_id, episode_number)
            return
        except Exception as e:
            logger.error(f"Error procesando solicitud de capítulo: {e}")
            await query.answer(f"Error: {str(e)[:200]}")
            return

    # Manejar solicitudes de todos los capítulos
    elif data.startswith("ep_all_"):
        try:
            # Formato: ep_all_[series_id]
            _, series_id = data.split("_all_")
            series_id = int(series_id)
            
            await send_all_episodes(query, context, series_id)
            return
        except Exception as e:
            logger.error(f"Error procesando solicitud de todos los capítulos: {e}")
            await query.answer(f"Error: {str(e)[:200]}")
            return
    
    # Verificar membresía antes de procesar otros callbacks
    user_id = query.from_user.id
    is_member = await is_channel_member(user_id, context)
    
    if not is_member and data not in ["verify_membership"]:
        # Usuario no es miembro, mostrar mensaje de suscripción
        keyboard = [
            [InlineKeyboardButton("Unirse al Canal 📢", url=f"https://t.me/multimediatvOficial")],
            [InlineKeyboardButton("Ya me uní ✅", callback_data="verify_membership")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚠️ Para usar el bot, debes unirte a nuestro canal principal.\n\n"
            "1. Haz clic en el botón 'Unirse al Canal 📢'\n"
            "2. Una vez unido, vuelve aquí y presiona 'Ya me uní ✅'",
            reply_markup=reply_markup
        )
        return
        
    if data == "profile":
        await handle_profile(update, context)
    elif data == "plans":
        await handle_plans(update, context)
    elif data == "info":
        await handle_info(update, context)
    elif data == "main_menu":
        # Recrear el mensaje de menú principal sin usar start
        user = query.from_user
        keyboard = [
            [
                InlineKeyboardButton("Multimedia Tv 📺", url=f"https://t.me/multimediatvOficial"),
                InlineKeyboardButton("Pedidos 📡", url=f"https://t.me/+X9S4pxF8c7plYjYx")
            ],
            [InlineKeyboardButton("Perfil 👤", callback_data="profile")],
            [InlineKeyboardButton("Planes 📜", callback_data="plans")],
            [InlineKeyboardButton("Información 📰", callback_data="info")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                f"¡Hola! {user.first_name}👋 te doy la bienvenida\n\n"
                f"MultimediaTv un bot donde encontraras un amplio catálogo de películas y series, "
                f"las cuales puedes buscar o solicitar en caso de no estar en el catálogo",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error returning to main menu: {e}")
            # Si falla el edit_message, intentamos enviar un nuevo mensaje
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat.id,
                    text=f"¡Hola! {user.first_name}👋 te doy la bienvenida\n\n"
                         f"MultimediaTv un bot donde encontraras un amplio catálogo de películas y series, "
                         f"las cuales puedes buscar o solicitar en caso de no estar en el catálogo",
                    reply_markup=reply_markup
                )
            except Exception as inner_e:
                logger.error(f"Error sending new main menu message: {inner_e}")
                await query.answer("Error al mostrar el menú principal. Intenta con /start")
                
    elif data in ["plan_pro", "plan_plus", "plan_ultra"]:
        await handle_plan_details(update, context)
    elif "_cup" in data or "_crypto" in data:
        await handle_payment_method(update, context)
    elif data.startswith("req_"):
        await handle_request_type(update, context)
    elif data == "make_request":
        await handle_make_request(update, context)
    elif data.startswith("accept_req_"):
        await handle_accept_request(update, context)
    elif data.startswith("send_"):
        # Get the message ID from the callback data
        try:
            msg_id = int(data.split("_")[1])
            await handle_send_callback(query, context, msg_id)
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing send callback data: {e}")
            await query.answer("Error: formato de datos inválido")
    else:
        await query.answer("Opción no disponible.")

async def check_plan_expiry(context: ContextTypes.DEFAULT_TYPE):
    """Background task to check for expired plans"""
    try:
        # Get users with expired plans
        expired_users = db.get_expired_plans()
        
        for user_id in expired_users:
            # Reset user to basic plan
            db.update_plan(user_id, 'basic', None)
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Tu plan premium ha expirado. Has sido cambiado al plan básico.\n"
                         "Para renovar tu plan, utiliza el botón 'Planes 📜' en el menú principal."
                )
            except Exception as e:
                logger.error(f"Error notifying user {user_id} about plan expiry: {e}")
    except Exception as e:
        logger.error(f"Error in plan expiry check: {e}")

async def reset_daily_limits(context: ContextTypes.DEFAULT_TYPE):
    """Background task to reset daily limits at midnight"""
    try:
        # Reset daily limits
        db.reset_daily_limits()
        logger.info("Daily limits reset")
    except Exception as e:
        logger.error(f"Error in daily limits reset: {e}")

async def error_handler(update, context):
    """Handle errors in the dispatcher"""
    logger.error(f"Exception while handling an update: {context.error}")

    # Log the error before we do anything else
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # Send a message to the user
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text="Ocurrió un error al procesar tu solicitud. Por favor, intenta nuevamente."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")
            
async def check_channel_memberships(context: ContextTypes.DEFAULT_TYPE):
    """Tarea periódica para verificar membresías y limpiar el caché."""
    try:
        verification_cache = context.bot_data.get('verification_cache', {})
        current_time = datetime.now()
        
        # Limpiar entradas expiradas del caché
        expired_keys = [k for k, v in verification_cache.items() if current_time > v]
        for key in expired_keys:
            verification_cache.pop(key, None)
            
        logger.info(f"Limpieza de caché de verificación: {len(expired_keys)} entradas eliminadas")
    except Exception as e:
        logger.error(f"Error en check_channel_memberships: {e}")
    
def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(TOKEN).build()
    
    application.bot_data['verification_cache'] = {}

    # Register error handler
    application.add_error_handler(error_handler)

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("search", search_content))
    application.add_handler(CommandHandler("imdb", imdb_command))
    application.add_handler(CommandHandler("down", down_command))
    application.add_handler(CommandHandler("plan", set_user_plan))
    application.add_handler(CommandHandler("upser", upser_command))
    application.add_handler(CommandHandler("cancelupser", cancel_upser_command))
    application.add_handler(CommandHandler("addgift_code", add_gift_code))
    application.add_handler(CommandHandler("gift_code", redeem_gift_code))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("up", upload_content))
    application.add_handler(CommandHandler("pedido", request_content))
    application.add_handler(CommandHandler("admin_help", admin_help))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    
    # Add message handler for direct text searches
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_search
    ))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    application.add_handler(MessageHandler(
    (filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
    handle_upser_input,
    # Este manejador debe ejecutarse después de otros manejadores más específicos
), group=1)
    
    # Schedule periodic tasks - Solución alternativa
    # En lugar de run_daily, usamos run_repeating con un intervalo de 24h
    application.job_queue.run_repeating(
        check_plan_expiry,
        interval=24*60*60,  # 24 horas en segundos
        first=60            # Esperar 60 segundos antes de la primera ejecución
    )
    
    application.job_queue.run_repeating(
        check_channel_memberships,
        interval=6*60*60,  # 6 horas en segundos
        first=600  # Primera ejecución después de 10 minutos
    )
    
    application.job_queue.run_repeating(
        reset_daily_limits,
        interval=24*60*60,  # 24 horas en segundos
        first=120           # Esperar 120 segundos antes de la primera ejecución
    )
    
    # Start the Bot
    application.run_polling()
    
    # Start Flask server to keep bot alive on Render
    keep_alive()

if __name__ == "__main__":
    main()
