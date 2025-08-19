

@dp.message(F.from_user.id.in_(list(ADMIN_IDS)))
async def admin_router(message: types.Message, state: FSMContext):
# Відповідь на конкретний тред
if message.reply_to_message and message.reply_to_message.message_id:
# Знайдемо тред по admin_message_id
admin_msg_id = message.reply_to_message.message_id
with db.cursor() as cur:
cur.execute(
"SELECT user_id FROM operator_threads WHERE admin_message_id=%s ORDER BY id DESC LIMIT 1",
(admin_msg_id,),
)
row = cur.fetchone()
if row:
uid = int(row["user_id"])
try:
if message.photo:
await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption or "")
else:
await bot.send_message(uid, message.text or "")
await message.reply("Надіслано користувачу")
return
except TelegramForbiddenError:
await message.reply("Користувач заблокував бота або недоступний")
return
except Exception as e:
await message.reply(f"Помилка відправки: {e}")
return


# Інші адмінські дії
if message.text == "Зробити розсилку":
await message.answer("Надішліть текст або фото з підписом для розсилки.")
await state.set_state(SendBroadcast.waiting_content)




# Хендлери стану розсилки
@dp.message(SendBroadcast.waiting_content, F.from_user.id.in_(list(ADMIN_IDS)), F.photo)
async def broadcast_photo(message: types.Message, state: FSMContext):
await do_broadcast(photo_id=message.photo[-1].file_id, caption=message.caption or "")
await state.clear()




@dp.message(SendBroadcast.waiting_content, F.from_user.id.in_(list(ADMIN_IDS)))
async def broadcast_text(message: types.Message, state: FSMContext):
await do_broadcast(text=message.text or "")
await state.clear()




async def do_broadcast(text: str = "", photo_id: Optional[str] = None, caption: str = ""):
users = get_all_subscribers()
ok = 0
blocked = 0


for uid in users:
try:
if photo_id:
await bot.send_photo(uid, photo_id, caption=caption)
else:
await bot.send_message(uid, text)
ok += 1
except TelegramRetryAfter as e:
await asyncio.sleep(e.retry_after + 1)
continue
except (TelegramForbiddenError, TelegramBadRequest):
blocked += 1
remove_subscriber(uid)
except Exception as e:
logger.warning(f"Broadcast to {uid} failed: {e}")
await asyncio.sleep(0.05) # анти-флуд


await notify_admin(f"Розсилка завершена. Успішно: {ok}, видалено зі списку: {blocked}.")




# ---------------------------------------------------------------------------
# Точка входу
# ---------------------------------------------------------------------------
async def main():
try:
await dp.start_polling(bot)
finally:
db.close()




if __name__ == "__main__":
asyncio.run(main())

