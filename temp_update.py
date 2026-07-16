from bot.settings_store import load_settings
rs = load_settings("dry")
print("Before:", rs.sl_atr_mult, rs.tp_atr_mult, rs.conf_min, rs.enabled, rs.technique)

from bot.config import load_settings as load_config
cfg = load_config()
print("Config:", cfg["risk"]["sl_atr_mult"], cfg["risk"]["tp_atr_mult"], cfg["signals"]["entry_confidence"], cfg["signals"]["adx_trend_min"])

rs.sl_atr_mult = cfg["risk"]["sl_atr_mult"]
rs.tp_atr_mult = cfg["risk"]["tp_atr_mult"]
rs.conf_min = cfg["signals"]["entry_confidence"]
rs.adx_trend_min = cfg["signals"]["adx_trend_min"]
rs.enabled = True
rs.technique = "rules"

rs.save()
print("Updated and saved")