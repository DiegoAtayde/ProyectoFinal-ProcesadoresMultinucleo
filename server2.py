import http.server
import socketserver
import cv2
import os

PORT = 8000

# ====== Generar imagen en blanco y negro ======
original = "messi.jpg"
byn = "messi_byn.jpg"

if not os.path.exists(byn):
    print("Generando imagen en blanco y negro...")
    img = cv2.imread(original)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cv2.imwrite(byn, gray)
    print("Imagen en blanco y negro generada exitosamente.")
else:
    print("Imagen en blanco y negro ya existe.")

# ====== Servidor HTTP simple ======
class MyHandler(http.server.SimpleHTTPRequestHandler):
    pass  # Solo sirve archivos de la carpeta actual

with socketserver.TCPServer(("0.0.0.0", PORT), MyHandler) as httpd:
    print(f"Servidor activo en el puerto: {PORT}")
    print(f"Acceder a la imagen original: http://192.168.4.2:{PORT}/{original}")
    print(f"Acceder a la imagen blanco y negro: http://192.168.4.2:{PORT}/{byn}")
    httpd.serve_forever()
