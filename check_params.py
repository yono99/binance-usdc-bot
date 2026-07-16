from bot.settings_store import load_settings
rs = load_settings("dry")
print(rs.params())