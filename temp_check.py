from bot.settings_store import load_settings
rs = load_settings("dry")
print("enabled:", rs.enabled, "tech:", rs.technique, "params:", rs.params())