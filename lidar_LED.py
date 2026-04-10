import time
import serial
import socket
import math
from rpi_ws281x import *

# ================= CONFIGURACIÓN =================
# --- SENSOR TFMINI ---
PUERTO_SERIAL = '/dev/serial0'
BAUDIOS = 115200

# RANGOS (Ajustado para ignorar el piso a 159cm)
RANGO_MIN = 40     
RANGO_MAX = 150    

# --- LUCES LED ---
LED_COUNT      = 1200     # 20 metros
LED_PIN        = 18       # GPIO 18
LED_FREQ_HZ    = 800000
LED_DMA        = 10
LED_BRIGHTNESS = 150      # Brillo medio/alto
LED_INVERT     = False
LED_CHANNEL    = 0

# --- RED UDP ---
UDP_IP = "172.16.70.160"
UDP_PORT = 5000

# --- TIEMPOS ---
TIEMPO_PARA_ACTIVAR = 2.0   # <--- Confirmado: 2 segundos de "carga"
TIEMPO_COOLDOWN     = 30.0  # <--- Confirmado: 30 segundos de video

# Tolerancia: Tiempo que aguantamos sin datos antes de cancelar la carga (evita parpadeos)
TOLERANCIA_PERDIDA  = 0.5   

# SECUENCIA DE COLORES Y COMANDOS
SECUENCIA = [
    {"cmd": b"Rojo",  "color": Color(255, 0, 0)},
    {"cmd": b"Verde", "color": Color(0, 255, 0)},
    {"cmd": b"Azul",  "color": Color(0, 0, 255)}
]

# ================= INICIALIZACIÓN =================
print(f"--- SISTEMA FINAL ---")
print(f"Carga: {TIEMPO_PARA_ACTIVAR}s | Video: {TIEMPO_COOLDOWN}s")
print(f"Rango: {RANGO_MIN}-{RANGO_MAX}cm")

# 1. Iniciar Luces
strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
strip.begin()

# 2. Iniciar Sensor
try:
    ser = serial.Serial(PUERTO_SERIAL, BAUDIOS, timeout=0.1)
except Exception as e:
    print(f"Error Hardware Sensor: {e}")
    exit()

# 3. Iniciar Red
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ================= FUNCIONES =================

def leer_sensor_fresco():
    """Borra buffer y trae el dato más nuevo posible con validación"""
    ser.reset_input_buffer()
    time.sleep(0.025) # Pequeña espera para llenar buffer nuevo
    
    if ser.in_waiting >= 9:
        try:
            if ser.read(1) == b'\x59' and ser.read(1) == b'\x59':
                datos = ser.read(7)
                if len(datos) == 7:
                    checksum = (0x59 + 0x59 + sum(datos[0:6])) % 256
                    if datos[6] == checksum:
                        return datos[0] + (datos[1] * 256)
        except:
            pass
    return None

def limpiar_luces():
    for i in range(strip.numPixels()): strip.setPixelColor(i, Color(0,0,0))
    strip.show()

def efecto_standby_suave():
    """Modo reposo ultra ligero. Las luces ya están apagadas, solo descansamos un poco"""
    time.sleep(0.02)  # Pausa cortísima (20ms) para no quemar CPU, manteniendo al sensor ultra rápido

def efecto_cargando(progreso):
    """Barra de carga que se llena (Color Ámbar/Dorado)"""
    total = strip.numPixels()
    limite = int(total * progreso)
    COLOR_CARGA = Color(255, 140, 0) # Naranja/Dorado tipo "Cargando"
    
    for i in range(total):
        if i < limite: strip.setPixelColor(i, COLOR_CARGA)
        else: strip.setPixelColor(i, Color(0,0,0))
    strip.show()

def poner_color_solido(color):
    """Pone toda la tira de un color fijo"""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
    strip.show()

# ================= PRINCIPAL =================
limpiar_luces()
ESTADO = "ESPERANDO" # Estados: ESPERANDO, CARGANDO, COOLDOWN
inicio_validacion = 0
inicio_cooldown = 0
indice_secuencia = 0
ultimo_tiempo_valido = 0 

try:
    while True:
        ahora = time.time()
        
        # -----------------------------------------------------------
        # ESTADO: COOLDOWN (Reproduciendo Video)
        # -----------------------------------------------------------
        if ESTADO == "COOLDOWN":
            restante = TIEMPO_COOLDOWN - (ahora - inicio_cooldown)
            
            if restante <= 0:
                print("\n>>> FIN VIDEO. Volviendo a buscar clientes.")
                ESTADO = "ESPERANDO"
                limpiar_luces()
            else:
                # Opcional: Imprimir cada tanto cuánto falta
                pass 
                # NOTA: Aquí NO leemos el sensor, dejamos que la gente vea el video tranquila.
                # Las luces se mantienen en el color que seteamos al activar.
                time.sleep(0.1)
                continue

        # -----------------------------------------------------------
        # LECTURA DE SENSOR (Solo si NO estamos en Cooldown)
        # -----------------------------------------------------------
        dist = leer_sensor_fresco()
        
        # Lógica de "Presencia" con Tolerancia
        detectado_ahora = False
        if dist is not None:
            if RANGO_MIN <= dist <= RANGO_MAX:
                detectado_ahora = True
                ultimo_tiempo_valido = ahora
                # print(f"Distancia: {dist} cm") # Descomentar para debug
        
        # La persona está presente si la detectamos AHORA o hace menos de 0.5s
        persona_presente = detectado_ahora or (ahora - ultimo_tiempo_valido < TOLERANCIA_PERDIDA)

        # -----------------------------------------------------------
        # MAQUINA DE ESTADOS (Esperando -> Cargando)
        # -----------------------------------------------------------
        
        if ESTADO == "ESPERANDO":
            if persona_presente:
                print(">>> Cliente detectado. Iniciando Carga...")
                ESTADO = "CARGANDO"
                inicio_validacion = ahora
            else:
                efecto_standby_suave()

        elif ESTADO == "CARGANDO":
            if persona_presente:
                # Calcular porcentaje (0.0 a 1.0)
                tiempo_transcurrido = ahora - inicio_validacion
                progreso = tiempo_transcurrido / TIEMPO_PARA_ACTIVAR
                
                if progreso > 1.0: progreso = 1.0
                
                # Dibujar barra
                efecto_cargando(progreso)
                
                # CHEQUEO DE FINALIZACIÓN
                if progreso >= 1.0:
                    # Preparar datos del evento actual
                    dato = SECUENCIA[indice_secuencia]
                    cmd = dato['cmd']
                    color = dato['color']
                    
                    print(f"\n>>> ¡ACTIVADO! Enviando: {cmd}")
                    
                    # 1. Enviar UDP
                    sock.sendto(cmd, (UDP_IP, UDP_PORT))
                    
                    # 2. Poner luces del color del video
                    poner_color_solido(color)
                    
                    # 3. Cambiar estado
                    ESTADO = "COOLDOWN"
                    inicio_cooldown = ahora
                    
                    # 4. Preparar siguiente color para la próxima
                    indice_secuencia = (indice_secuencia + 1) % len(SECUENCIA)
            
            else:
                # Si deja de detectar (y pasó la tolerancia), cancelamos
                print("Cliente se retiró. Reset.")
                limpiar_luces()
                ESTADO = "ESPERANDO"

except KeyboardInterrupt:
    limpiar_luces()
    ser.close()
    sock.close()
    print("\nSistema Apagado.")
