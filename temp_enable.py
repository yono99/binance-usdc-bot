from bot.settings_store import load_settings
rs = load_settings("dry")
print("Before:", rs.enabled, rs.technique, rs.params())

rs.enabled = True
rs.technique = "auto"
rs.save()

print("After:", rs.enabled, rs.technique, rs.params())