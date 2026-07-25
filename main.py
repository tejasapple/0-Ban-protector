# pip install motor python-telegram-bot
import os
import asyncio
import csv
import io
import logging
import html  # Added to fix HTML formatting issues with usernames
from datetime import datetime
from typing import Optional, Dict, Any

import telegram
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatJoinRequest,
    BotCommand
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatJoinRequestHandler,
    ContextTypes,
    filters,
)

# ==========================================
# 🛠️ LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# ⚙️ BACKGROUND TASK MANAGER (FIX FOR SILENT CRASHES)
# ==========================================
active_tasks = set()

def run_in_background(coroutine):
    """
    Runs a coroutine safely in the background.
    This prevents Python's Garbage Collector from killing the broadcast mid-execution.
    """
    task = asyncio.create_task(coroutine)
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)
    return task

# ==========================================
# ⚙️ CONFIGURATION (अपनी डिटेल्स यहाँ डालें)
# ==========================================
BOT_TOKEN = "8699037644:AAEmEdtcs1gzrcMgkhncp_aVcf6el19Ohow"
MONGO_DB_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0" 
ADMIN_ID = 8884734704  

# ==========================================
# 🗄️ DATABASE SETUP (MongoDB)
# ==========================================
logger.info("Connecting to MongoDB...")
db_client = AsyncIOMotorClient(MONGO_DB_URI)
db = db_client["AutoAcceptBot"]
users_col = db["users"]
chats_col = db["chats"]
settings_col = db["settings"] # New Collection for Admin Settings

# State Managers (Memory) for DM Setup & Broadcasts
setup_state: Dict[int, Dict[str, Any]] = {}
bcast_state: Dict[int, Dict[str, Any]] = {}

# ==========================================
# 🗃️ DATABASE HELPER FUNCTIONS
# ==========================================
async def save_user(user: telegram.User) -> None:
    """Saves or updates a user in the database without deleting old data."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await users_col.update_one(
            {"user_id": user.id}, 
            {
                "$set": {
                    "name": user.first_name,
                    "username": user.username or "None",
                    "last_active": today
                }, 
                "$setOnInsert": {"date": today}
            }, 
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving user {user.id}: {e}")

async def save_chat(chat: telegram.Chat) -> None:
    """Saves or updates a chat in the database."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await chats_col.update_one(
            {"chat_id": chat.id}, 
            {
                "$set": {
                    "title": chat.title,
                    "username": chat.username or "None",
                    "type": chat.type,
                    "last_active": today
                }, 
                "$setOnInsert": {"date": today}
            }, 
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving chat {chat.id}: {e}")

# ==========================================
# 🎨 COLOR BUTTONS HELPER (PTB api_kwargs)
# ==========================================
def normalize_style(value: str) -> str:
    """Normalizes color string for Telegram inline buttons."""
    value = (value or "").strip().lower()
    if value in {"success", "green", "paid"}:
        return "success"
    if value in {"danger", "red", "delete", "disable"}:
        return "danger"
    if value in {"default", "gray", "grey", "cancel"}:
        return "default"
    return "primary"

def get_color_btn(text: str, callback_data: Optional[str] = None, url: Optional[str] = None, style: str = "primary") -> InlineKeyboardButton:
    """Helper method to generate an InlineKeyboardButton with dynamic color support."""
    kwargs = {"api_kwargs": {"style": normalize_style(style)}}
    if url:
        return InlineKeyboardButton(text=text, url=url, **kwargs)
    return InlineKeyboardButton(text=text, callback_data=callback_data, **kwargs)

# ==========================================
# 🚀 CORE VERIFICATION PROCESSOR
# ==========================================
async def process_verification(uid: int, context: ContextTypes.DEFAULT_TYPE):
    """Sends the custom DM configuration after user verification."""
    custom_dm = await settings_col.find_one({"_id": "custom_dm"})
    
    if custom_dm:
        state = custom_dm["data"]
        inline_buttons = []
        for btn in state.get("buttons", []):
            inline_buttons.append([get_color_btn(btn["name"], url=btn["url"], style=btn["style"])])
            
        kb = InlineKeyboardMarkup(inline_buttons) if inline_buttons else None
        msg_text = state.get("text") or ""
        
        try:
            if state.get("media_type") == "photo":
                await context.bot.send_photo(chat_id=uid, photo=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state.get("media_type") == "video":
                await context.bot.send_video(chat_id=uid, video=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state.get("media_type") == "document":
                await context.bot.send_document(chat_id=uid, document=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state.get("media_type") == "audio":
                await context.bot.send_audio(chat_id=uid, audio=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state.get("media_type") == "animation":
                await context.bot.send_animation(chat_id=uid, animation=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state.get("media_type") == "voice":
                await context.bot.send_voice(chat_id=uid, voice=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=uid, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Error sending custom DM to {uid}: {e}")
    else:
        await context.bot.send_message(
            chat_id=uid,
            text="✅ <b>Verification Successful!</b>\n\nYour identity has been verified. Please wait for admins to review your request.",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# 🚀 START COMMAND & HELP
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    message = update.message
    user = message.from_user
    bot = context.bot
    
    await save_user(user)
    safe_name = html.escape(user.first_name)
    
    # 🎯 DEEP LINK INTERCEPTOR: Handles instant verification without asking to click start again
    if context.args and context.args[0].startswith("verify_"):
        await message.reply_text("✅ Identity Confirmed! Sending details...", parse_mode=ParseMode.HTML)
        await process_verification(user.id, context)
        return
    
    admin_rights = "invite_users+manage_chat+restrict_members+promote_members+change_info+post_messages+edit_messages+delete_messages"
    
    keyboard = InlineKeyboardMarkup([
        [get_color_btn("➕ Add to your Group", url=f"https://t.me/{bot.username}?startgroup=true&admin={admin_rights}", style="success")],
        [get_color_btn("📢 Add to your Channel", url=f"https://t.me/{bot.username}?startchannel=true&admin={admin_rights}", style="primary")]
    ])
    
    text = (
        f"<blockquote>🛡️ <b>GROUP BAN PROTECTOR [ADVANCED V2]</b></blockquote>\n\n"
        f"Hello <b>{safe_name}</b>!\n\n"
        f"मैं 90% तक फेक रिपोर्ट्स अपने ऊपर ले लेता हूँ। मतलब 90% ये बोट आपके ग्रुप को सेव कर लेगा। 🛑\n\n"
        f"💎 <b>VIP PROTECTION:</b> Add me to your group and just simply I am working with AI. Simple se mere ko apne group me add kar lo, I will work silently. You won't face any issues at all!\n\n"
        f"<i>⚠️ Note: Please make sure 'Remain Anonymous' permission is turned OFF so I can work properly.</i>"
    )
    
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /help command."""
    user = update.message.from_user
    await save_user(user)
    
    text = (
        f"<blockquote>🛡️ <b>PROTECTOR HELP CENTER</b></blockquote>\n\n"
        f"<b>How to use me?</b>\n"
        f"1. Add me to your Group or Channel.\n"
        f"2. Promote me as an Admin with 'Invite Users' rights.\n"
        f"3. Turn on 'Approve New Members' in your group/channel settings.\n\n"
        f"I will filter out bots and scripts by verifying real members automatically via AI!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ==========================================
# 🛡️ VERIFICATION DM (STEP 1) - FIXED (SUPER FAST)
# ==========================================
async def auto_accept_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles new chat join requests and sends Verification DM instantly."""
    request = update.chat_join_request
    chat = request.chat
    user = request.from_user
    
    safe_name = html.escape(user.first_name)
    bot_username = context.bot.username
    
    text = (
        f"<blockquote>⚠️ <b>Security Verification Required</b></blockquote>\n\n"
        f"Hello <b>{safe_name}</b>,\n\n"
        f"This group is using an Advanced version of Group Ban Protector to protect our groups from fake reports and bot scripts. 🛡️\n\n"
        f"Please verify that you are a real human to get your request approved."
    )
    
    # 🎯 FIX APPLIED: Using Deep Link URL instead of callback data to bypass START button issue
    verify_url = f"https://t.me/{bot_username}?start=verify_{chat.id}"
    
    keyboard = InlineKeyboardMarkup([
        [get_color_btn("🤖 I am not a robot (Verify)", url=verify_url, style="success")]
    ])
    
    # 🚀 FAST EXECUTION LOGIC: मैसेज भेजने का टास्क सबसे पहले रन होगा
    async def send_dm_instantly():
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                await context.bot.send_message(
                    chat_id=user.id, 
                    text=text, 
                    reply_markup=keyboard, 
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Verification DM sent successfully to {user.id}")
                break
                
            except telegram.error.RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                if attempt < max_retries:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Failed to DM {user.id}: {e}")

    # 1. तुरंत DM भेजो (बिना रुके) - Fixed task management
    run_in_background(send_dm_instantly())
    
    # 2. बैकग्राउंड में डेटाबेस सेव करो (इससे DM भेजने में देरी नहीं होगी)
    run_in_background(save_user(user))
    run_in_background(save_chat(chat))

# ==========================================
# ⚙️ ADVANCED ADMIN PANEL DASHBOARD
# ==========================================
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the Admin Dashboard."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
        
    keyboard = InlineKeyboardMarkup([
        [get_color_btn("📊 View Bot Live Stats", callback_data="admin_stats", style="primary")],
        [get_color_btn("⚙️ Set Post-Verify DM", callback_data="setup_dm", style="success"),
         get_color_btn("🗑️ Clear Custom DM", callback_data="clear_dm", style="danger")],
        [get_color_btn("📢 Broadcast to Users (DM)", callback_data="bcast_users", style="success")],
        [get_color_btn("📢 Broadcast to Groups/Channels", callback_data="bcast_chats", style="danger")]
    ])
    
    text = (
        f"<blockquote>⚙️ <b>ADVANCED ADMIN PANEL</b></blockquote>\n\n"
        f"Welcome to the Admin Dashboard. Manage statistics, configure the Custom DM, and use Broadcast features."
    )
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

# ==========================================
# 📊 EXPORT DATA TO CSV (ADMIN ONLY)
# ==========================================
async def export_users_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all users from MongoDB to a CSV file."""
    if update.effective_user.id != ADMIN_ID:
        return
        
    processing_msg = await update.message.reply_text("🔄 Fetching users data... Please wait.")
    users = await users_col.find({}).to_list(length=None)
    
    if not users:
        await processing_msg.edit_text("⚠️ No users found in database.")
        return
        
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Name", "Username", "Join Date", "Last Active"])
    
    for u in users:
        writer.writerow([
            u.get("user_id", "N/A"), 
            u.get("name", "N/A"), 
            u.get("username", "N/A"), 
            u.get("date", "N/A"),
            u.get("last_active", "N/A")
        ])
        
    output.seek(0)
    file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    file_bytes.name = f"Users_Export_{datetime.now().strftime('%Y%m%d')}.csv"
    
    await context.bot.send_document(
        chat_id=ADMIN_ID, 
        document=file_bytes, 
        caption=f"✅ <b>Users Export Completed</b>\nTotal Users: <code>{len(users)}</code>", 
        parse_mode=ParseMode.HTML
    )
    await processing_msg.delete()

# ==========================================
# 🎛️ CALLBACK QUERY ROUTER (ALL BUTTONS)
# ==========================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes all inline button clicks to their appropriate functions."""
    query = update.callback_query
    data = query.data
    user = query.from_user
    uid = user.id
    
    try:
        await query.answer()
    except:
        pass

    # 1. Fallback for older verification buttons before Deep Link update
    if data.startswith("verify_"):
        await save_user(user)
        try:
            await query.answer("✅ Identity Confirmed! Sending details...", show_alert=True)
            await query.message.delete()
        except:
            pass
        await process_verification(uid, context)
        return

    # 2. Admin Live Stats
    if data == "admin_stats" and uid == ADMIN_ID:
        today = datetime.now().strftime("%Y-%m-%d")
        total_users = await users_col.count_documents({})
        today_users = await users_col.count_documents({"date": today})
        total_chats = await chats_col.count_documents({})
        today_chats = await chats_col.count_documents({"date": today})
        
        text = (
            f"<blockquote>📊 <b>DATABASE LIVE STATISTICS</b></blockquote>\n\n"
            f"👤 <b>Total Verified Users:</b> <code>{total_users}</code>\n"
            f"🆕 <b>Today's New Users:</b> <code>{today_users}</code>\n\n"
            f"👥 <b>Total Groups/Channels:</b> <code>{total_chats}</code>\n"
            f"🆕 <b>Today's New Groups/Channels:</b> <code>{today_chats}</code>\n\n"
            f"<i>💡 Tip: Send /export_users to get full database in CSV.</i>"
        )
        keyboard = InlineKeyboardMarkup([[get_color_btn("⬅️ Back to Admin Panel", callback_data="back_to_admin", style="default")]])
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    # 3. Back to Admin Panel
    if data == "back_to_admin" and uid == ADMIN_ID:
        keyboard = InlineKeyboardMarkup([
            [get_color_btn("📊 View Bot Live Stats", callback_data="admin_stats", style="primary")],
            [get_color_btn("⚙️ Set Post-Verify DM", callback_data="setup_dm", style="success"),
             get_color_btn("🗑️ Clear Custom DM", callback_data="clear_dm", style="danger")],
            [get_color_btn("📢 Broadcast to Users (DM)", callback_data="bcast_users", style="success")],
            [get_color_btn("📢 Broadcast to Groups/Channels", callback_data="bcast_chats", style="danger")]
        ])
        text = (
            f"<blockquote>⚙️ <b>ADVANCED ADMIN PANEL</b></blockquote>\n\n"
            f"Welcome to the Admin Dashboard. Manage statistics, configure the Custom DM, and use Broadcast features."
        )
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return
        
    # 4. Clear Custom DM Setting
    if data == "clear_dm" and uid == ADMIN_ID:
        await settings_col.delete_one({"_id": "custom_dm"})
        keyboard = InlineKeyboardMarkup([[get_color_btn("⬅️ Back to Admin Panel", callback_data="back_to_admin", style="default")]])
        await query.message.edit_text("✅ <b>Custom DM Cleared!</b>\n\nBot will now send the default text message after verification.", reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    # 5. Initiate Custom DM Setup Flow
    if data == "setup_dm" and uid == ADMIN_ID:
        setup_state[uid] = {
            "step": "media",
            "media_type": None,
            "media_id": None,
            "text": None,
            "target_button_count": 0,
            "current_button_index": 0,
            "buttons": [],
            "temp_name": "",
            "temp_url": ""
        }
        text = (
            f"<blockquote>⚙️ <b>CUSTOM DM WIZARD</b></blockquote>\n\n"
            f"Let's configure the message users get AFTER clicking Verify.\n\n"
            f"<b>Step 1:</b> Send <b>Media (Photo/Video/Audio/Doc)</b> for the DM.\n\n"
            f"<i>(Type /skip if you only want to send a text message)</i>"
        )
        await query.message.edit_text(text, parse_mode=ParseMode.HTML)
        return

    # 6. Initiate Broadcast Flow (Users or Chats)
    if data in ["bcast_users", "bcast_chats"] and uid == ADMIN_ID:
        btype = "users" if data == "bcast_users" else "chats"
        bcast_state[uid] = {
            "type": btype,
            "step": "media",
            "media_type": None,
            "media_id": None,
            "text": None,
            "target_button_count": 0,
            "current_button_index": 0,
            "buttons": [],
            "temp_name": "",
            "temp_url": ""
        }
        target_name = "Verified Users (DM)" if btype == "users" else "Groups & Channels"
        text = (
            f"<blockquote>📢 <b>BROADCAST WIZARD</b></blockquote>\n\n"
            f"<b>Target:</b> {target_name}\n\n"
            f"<b>Step 1:</b> Send <b>Media (Photo/Video/Audio/Doc)</b> for the broadcast.\n\n"
            f"<i>(Type /skip if you only want to send a text message)</i>"
        )
        await query.message.edit_text(text, parse_mode=ParseMode.HTML)
        return

    # 7. Button Color Selection
    if data.startswith("setcol_") and uid == ADMIN_ID:
        color_choice = data.split("_")[1]
        
        state = None
        wizard_type = None
        
        if uid in setup_state and setup_state[uid]["step"] == "btn_color":
            state = setup_state[uid]
            wizard_type = "setup"
        elif uid in bcast_state and bcast_state[uid]["step"] == "btn_color":
            state = bcast_state[uid]
            wizard_type = "bcast"
        else:
            try: await query.answer("Session expired or invalid step.", show_alert=True)
            except: pass
            return
            
        state["buttons"].append({
            "name": state["temp_name"],
            "url": state["temp_url"],
            "style": color_choice
        })
        state["current_button_index"] += 1
        
        try: await query.message.delete()
        except: pass
        
        if state["current_button_index"] < state["target_button_count"]:
            state["step"] = "btn_name"
            next_num = state["current_button_index"] + 1
            await context.bot.send_message(
                uid, 
                f"✅ <b>Button {next_num-1} Configured!</b>\n\n📝 <b>Configuring Button {next_num}:</b>\nPlease send the <b>Name (Text)</b> for this button.", 
                parse_mode=ParseMode.HTML
            )
        else:
            state["step"] = "confirm"
            if wizard_type == "setup":
                await context.bot.send_message(
                    uid, 
                    "✅ <b>All Buttons Configured Successfully!</b>\n\nAll set! Type <b>/confirm</b> to save this Custom DM or <b>/cancel</b> to abort.", 
                    parse_mode=ParseMode.HTML
                )
            else:
                await context.bot.send_message(
                    uid, 
                    "✅ <b>All Buttons Configured Successfully!</b>\n\nAll set! Type <b>/confirm</b> to start the broadcast or <b>/cancel</b> to abort.", 
                    parse_mode=ParseMode.HTML
                )
        return

# ==========================================
# 📢 UNIFIED WIZARD PROCESSORS (Setup DM & Broadcast)
# ==========================================
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the ongoing setup or broadcast."""
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        if uid in setup_state:
            del setup_state[uid]
            await update.message.reply_text("❌ <b>Custom DM Setup Cancelled Successfully.</b>", parse_mode=ParseMode.HTML)
        elif uid in bcast_state:
            del bcast_state[uid]
            await update.message.reply_text("❌ <b>Broadcast Process Cancelled Successfully.</b>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("You don't have any active setup running.")

async def process_wizard_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles sequential steps for both Custom DM Setup and Broadcast Wizards."""
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
        
    state = None
    wizard_type = None
    
    if uid in setup_state:
        state = setup_state[uid]
        wizard_type = "setup"
    elif uid in bcast_state:
        state = bcast_state[uid]
        wizard_type = "bcast"
    else:
        return
        
    step = state["step"]
    message = update.message
    
    raw_text = message.text or message.caption or ""
    is_skip_cmd = raw_text.strip().lower() == "/skip"
    
    if step == "media":
        if is_skip_cmd:
            state["step"] = "text"
            await message.reply_text("⏭ <b>Media Skipped.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.photo:
            state["media_type"] = "photo"
            state["media_id"] = message.photo[-1].file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Photo Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.video:
            state["media_type"] = "video"
            state["media_id"] = message.video.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Video Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.document:
            state["media_type"] = "document"
            state["media_id"] = message.document.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Document Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.audio:
            state["media_type"] = "audio"
            state["media_id"] = message.audio.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Audio Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.animation:
            state["media_type"] = "animation"
            state["media_id"] = message.animation.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>GIF/Animation Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        elif message.voice:
            state["media_type"] = "voice"
            state["media_id"] = message.voice.file_id
            state["step"] = "text"
            await message.reply_text("✅ <b>Voice Note Saved.</b>\n\n<b>Step 2:</b> Now send the <b>Text Message</b>.\n<i>(Type /skip to skip text)</i>", parse_mode=ParseMode.HTML)
        else:
            await message.reply_text("⚠️ Please send a Media File (Photo/Video/Doc/Audio/GIF/Voice) or type /skip.")
            
    elif step == "text":
        if is_skip_cmd:
            if not state["media_id"]:
                await message.reply_text("⚠️ You cannot skip both Media and Text! Please send some text.")
                return
            state["text"] = None  
            state["step"] = "btn_count"
            await message.reply_text("⏭ <b>Text Skipped.</b>\n\n<b>Step 3:</b> How many URL Buttons do you want to add? (Send a number like 0, 1, 2, etc.)", parse_mode=ParseMode.HTML)
        else:
            state["text"] = message.text_html if message.text else (message.caption_html if message.caption else raw_text)
            state["step"] = "btn_count"
            await message.reply_text("✅ <b>Text Saved.</b>\n\n<b>Step 3:</b> How many URL Buttons do you want to add? (Send a number like 0, 1, 2, etc.)", parse_mode=ParseMode.HTML)
            
    elif step == "btn_count":
        try: 
            count = int(raw_text)
        except ValueError:
            await message.reply_text("⚠️ Please send a valid number (e.g., 0, 1, 2).")
            return
            
        if count == 0:
            state["step"] = "confirm"
            if wizard_type == "setup":
                await message.reply_text("✅ <b>No buttons selected.</b>\n\nAll set! Type <b>/confirm</b> to save this Custom DM or <b>/cancel</b> to abort.", parse_mode=ParseMode.HTML)
            else:
                await message.reply_text("✅ <b>No buttons selected.</b>\n\nAll set! Type <b>/confirm</b> to start the broadcast or <b>/cancel</b> to abort.", parse_mode=ParseMode.HTML)
        else:
            state["target_button_count"] = count
            state["current_button_index"] = 0
            state["step"] = "btn_name"
            await message.reply_text("📝 <b>Configuring Button 1:</b>\n\nPlease send the <b>Name (Text)</b> for this button.", parse_mode=ParseMode.HTML)
            
    elif step == "btn_name":
        state["temp_name"] = raw_text
        state["step"] = "btn_url"
        await message.reply_text("✅ <b>Name Saved.</b>\n\nNow send the <b>URL (Link)</b> for this button (must start with http:// or https://).", parse_mode=ParseMode.HTML)
        
    elif step == "btn_url":
        if not raw_text.startswith("http"):
            await message.reply_text("⚠️ Invalid Link! Please send a valid link starting with http:// or https://")
            return
            
        state["temp_url"] = raw_text
        state["step"] = "btn_color"
        
        kb = InlineKeyboardMarkup([
            [get_color_btn("🟢 Success (Green)", callback_data="setcol_success", style="success"), 
             get_color_btn("🔴 Danger (Red)", callback_data="setcol_danger", style="danger")],
            [get_color_btn("🔵 Primary (Blue)", callback_data="setcol_primary", style="primary"), 
             get_color_btn("⚪ Default (Gray)", callback_data="setcol_default", style="default")]
        ])
        await message.reply_text("🎨 <b>Select Button Color:</b>\n\nChoose a color for this button from the menu below:", reply_markup=kb, parse_mode=ParseMode.HTML)

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirms and Saves Custom DM OR Starts Broadcast."""
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        if uid in setup_state and setup_state[uid]["step"] == "confirm":
            state = setup_state[uid]
            await settings_col.update_one(
                {"_id": "custom_dm"},
                {"$set": {"data": state}},
                upsert=True
            )
            del setup_state[uid]
            await update.message.reply_text("🚀 <b>Custom Verification DM Saved Successfully!</b>\n\nAnyone who verifies now will receive this exact message and buttons.", parse_mode=ParseMode.HTML)
            
        elif uid in bcast_state and bcast_state[uid]["step"] == "confirm":
            state = bcast_state[uid].copy() # Copying state to avoid deletion conflicts
            await update.message.reply_text("🚀 <b>Broadcast Starting... Please wait. I will notify you when it finishes.</b>", parse_mode=ParseMode.HTML)
            
            # Using the safe background task manager
            run_in_background(execute_broadcast(context, uid, state))
            del bcast_state[uid]
            
        else:
            await update.message.reply_text("⚠️ No setup or broadcast is waiting for confirmation.")

# ==========================================
# 📢 BROADCAST EXECUTION ENGINE (FIXED BUGS)
# ==========================================
async def execute_broadcast(context: ContextTypes.DEFAULT_TYPE, admin_id: int, state: dict):
    """Executes the broadcast loop seamlessly with Async Cursors for High Scalability."""
    btype = state["type"]
    success, failed = 0, 0
    
    inline_buttons = []
    for btn in state["buttons"]:
        inline_buttons.append([get_color_btn(btn["name"], url=btn["url"], style=btn["style"])])
        
    kb = InlineKeyboardMarkup(inline_buttons) if inline_buttons else None
    msg_text = state["text"] if state["text"] else ""
    
    collection = users_col if btype == "users" else chats_col
    id_key = "user_id" if btype == "users" else "chat_id"
        
    try:
        # FIX 1: Fetching all targets to prevent MongoDB Cursor Timeout on long broadcasts
        targets = await collection.find({}).to_list(length=None)
    except Exception as e:
        logger.error(f"Failed to fetch broadcast targets: {e}")
        await context.bot.send_message(admin_id, text=f"❌ <b>Broadcast Error:</b> Failed to read database.\n{e}", parse_mode=ParseMode.HTML)
        return
    
    for target in targets:
        tid = target.get(id_key)
        if not tid:
            continue
            
        try:
            if state["media_type"] == "photo":
                await context.bot.send_photo(chat_id=tid, photo=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "video":
                await context.bot.send_video(chat_id=tid, video=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "document":
                await context.bot.send_document(chat_id=tid, document=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "audio":
                await context.bot.send_audio(chat_id=tid, audio=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "animation":
                await context.bot.send_animation(chat_id=tid, animation=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            elif state["media_type"] == "voice":
                await context.bot.send_voice(chat_id=tid, voice=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=tid, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            
            success += 1
            await asyncio.sleep(0.05)
            
        except telegram.error.RetryAfter as e:
            logger.warning(f"Broadcast FloodWait for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            try:
                if state["media_type"] == "photo":
                    await context.bot.send_photo(chat_id=tid, photo=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "video":
                    await context.bot.send_video(chat_id=tid, video=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "document":
                    await context.bot.send_document(chat_id=tid, document=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "audio":
                    await context.bot.send_audio(chat_id=tid, audio=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "animation":
                    await context.bot.send_animation(chat_id=tid, animation=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                elif state["media_type"] == "voice":
                    await context.bot.send_voice(chat_id=tid, voice=state["media_id"], caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(chat_id=tid, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
                success += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
                
        except Exception as e:
            logger.info(f"Broadcast to {tid} failed. Reason: {e}")
            failed += 1
            
    # FIX 2: Wrapped the final message in Try-Except to ensure the function finishes cleanly
    try:
        await context.bot.send_message(
            admin_id, 
            f"<blockquote>✅ <b>BROADCAST COMPLETED</b></blockquote>\n\n"
            f"🎯 <b>Successfully Sent:</b> <code>{success}</code>\n"
            f"🚫 <b>Failed (Blocked/Dead):</b> <code>{failed}</code>\n\n"
            f"<i>Note: Failed users are NOT deleted from the database. Their data is safe.</i>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to send confirmation to admin {admin_id}. Error: {e}")

# ==========================================
# ⚙️ BOT INITIALIZATION & COMMAND SETUP
# ==========================================
async def post_init(application: Application):
    """Sets up the bot commands menu automatically."""
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Get help and instructions"),
        BotCommand("admin", "Open Admin Panel")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands successfully updated!")

# ==========================================
# 🏃 RUN THE BOT
# ==========================================
def main():
    logger.info("Bot is Starting... ✅")
    
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("admin", admin_dashboard, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("cancel", cancel_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("confirm", confirm_handler, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("export_users", export_users_csv, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("skip", process_wizard_steps, filters=filters.ChatType.PRIVATE))
    
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(ChatJoinRequestHandler(auto_accept_requests))
    
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (
            filters.PHOTO | filters.VIDEO | filters.TEXT | filters.Document.ALL | 
            filters.AUDIO | filters.ANIMATION | filters.VOICE
        ) & ~filters.COMMAND, 
        process_wizard_steps
    ))
    
    app.run_polling(allowed_updates=["message", "callback_query", "chat_join_request"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
