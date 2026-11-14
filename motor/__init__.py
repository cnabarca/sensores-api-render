"""Monitoreo predictivo de un motor de inducción de 30 kW + bomba centrífuga, servido en la nube (Semana 3)."""
import sys

# La consola de Windows usa cp1252 por defecto y falla con caracteres como σ, ≥ o tildes.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
