import time
import json
import _thread
import os

SSID = "ESP32_MQ2"
PASSWORD = "12345678"

# IP/PORT de la Raspberry Pi
PI_IP = "192.168.4.2"
PI_PORT = "8000"

svNCID = 0
lkNCID = _thread.allocate_lock()

uart = None
lkUART = _thread.allocate_lock()

svRPCResponses = {}
lkRPCResponses = _thread.allocate_lock()

SENSOR = {"ppm":0.0, "rs":0.0}
HISTORY = []
HISTORY_LOCK = _thread.allocate_lock()

#Parametros MQ2
MQ2 = {
    "VCC_SENSOR": 5.0,
    "R_DIV_TOP": 50000.0,   # R1=50k
    "R_DIV_BOT": 100000.0,  # R2=100k
    "RL": 1000.0,           # RL=1k
    "M_CURVE_M": -0.45,
    "M_CURVE_B": 1.25,
    "R0": 1000.0            
}

def setupUART():
    global uart
    pin_tx = machine.Pin(17, machine.Pin.OUT)
    pin_rx = machine.Pin(16, machine.Pin.IN)
    uart = machine.UART(2,
        baudrate=115200,
        tx=17, rx=16,
        timeout=1,
        timeout_char=1)

def configurar_wifi():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=SSID, password=PASSWORD)
    ap.ifconfig(("192.168.4.1","255.255.255.0","192.168.4.1","8.8.8.8"))
    print("AP creado:", SSID, "IP:", ap.ifconfig()[0])
    return ap

def configurar_adc():
    adc = machine.ADC(machine.Pin(36))
    adc.atten(machine.ADC.ATTN_11DB)
    adc.width(machine.ADC.WIDTH_12BIT)
    return adc

# Conversiones MQ-2

def raw_to_vadc(raw):
    return raw * (3.3 / 4095.0)

def vadc_to_vsensor(v_adc, p=MQ2):
    factor = (p["R_DIV_TOP"] + p["R_DIV_BOT"]) / p["R_DIV_BOT"]
    return v_adc * factor

def compute_rs(v_sensor, p=MQ2):
    Vcc = p["VCC_SENSOR"]
    RL = p["RL"]
    if v_sensor <= 0.001:
        return 1e9
    if v_sensor >= Vcc:
        return 1e-6
    return RL * (Vcc - v_sensor) / v_sensor

def mq2_ppm_butano(rs, p=MQ2):
    R0 = p["R0"]
    if R0 <= 0:
        return 0.0
    ratio = rs / R0
    try:
        ppm = 10 ** ((math.log10(ratio) - p["M_CURVE_B"]) / p["M_CURVE_M"])
        return ppm
    except:
        return 0.0

# RPC

def indexOf(s, c, offset=0):
    for i in range(offset, len(s)):
        if s[i] == c:
            return i
    return -1

def rpcReqS(func, *args):
    global svNCID, lkNCID
    if not isinstance(func, str): return None
    if args:
        sparams = ','.join([str(a) for a in args])
    else:
        sparams = ''
    with lkNCID:
        cid = svNCID
        svNCID += 1
    reqid = f'{cid}:{func}'
    with lkUART:
        uart.write(f'{reqid}:{sparams}\n')
    return reqid

def rpcResW(reqid, timeout=1000):
    elapsed = 0
    while elapsed < timeout:
        with lkRPCResponses:
            if reqid in svRPCResponses:
                r = svRPCResponses[reqid]
                del svRPCResponses[reqid]
                return r
        time.sleep(0.001)
        elapsed += 1
    return None

def rpc(func, *args):
    reqid = rpcReqS(func, *args)
    return rpcResW(reqid)

def rpcTask(dummy=None):
    global svRPCResponses
    print("rpcTask iniciado (escuchando UART)...")
    while True:
        with lkUART:
            line = uart.readline()
        if not line:
            time.sleep(0.001)
            continue
        try:
            line = line.decode('utf-8').strip()
        except:
            continue
        if len(line) < 3:
            continue
        # parse: id:function:result
        sc1 = indexOf(line, ':', 0)
        if sc1 == -1: continue
        sc2 = indexOf(line, ':', sc1+1)
        if sc2 == -1: continue
        reqid = line[:sc2]
        result = line[sc2+1:]
        with lkRPCResponses:
            svRPCResponses[reqid] = result
        # loop

def thread_sensor(adc, params):
    global HISTORY
    print("Hilo sensor iniciado")
    while True:
        raw = adc.read()
        v_adc = raw_to_vadc(raw)
        v_sensor = vadc_to_vsensor(v_adc, params)
        rs = compute_rs(v_sensor, params)
        ppm = mq2_ppm_butano(rs, params)
        sample = {"ppm": float(ppm)}
        with HISTORY_LOCK:
            HISTORY.append(sample)
            if len(HISTORY) > 60:
                HISTORY = HISTORY[-60:]
        time.sleep(1)


def read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except:
        return None

def render_index():
    txt = read_file("index.html")
    if txt is None:
        return "<h1>index.html no encontrado</h1>"
    # Reemplazar marcadores
    txt = txt.replace("{{PI_IP}}", PI_IP)
    txt = txt.replace("{{PI_PORT}}", PI_PORT)
    # led status placeholder <!--led--> : obtener por RPC
    try:
        led = rpc('led')
        if led is None:
            led = "-"
    except:
        led = "-"
    txt = txt.replace("<!--led-->", str(led), 1)
    return txt

def serveWeb():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ip = network.WLAN(network.AP_IF).ifconfig()[0]
    s.bind((ip, 80))
    s.listen(5)
    print("Servidor HTTP escuchando en", ip, ":80")
    while True:
        conn, addr = s.accept()
        try:
            req = conn.recv(2048)
            if not req:
                conn.close()
                continue
            try:
                sreq = req.decode()
            except:
                conn.close()
                continue
            first_line = sreq.split('\n')[0]
            # parse path
            parts = first_line.split(' ')
            if len(parts) < 2:
                conn.close(); continue
            path = parts[1]
            # root
            if path == "/" or path == "/index.html":
                html = render_index()
                conn.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
                conn.sendall(html.encode())
                conn.close(); continue
                
            if path.startswith("/gas"):
                with HISTORY_LOCK:
                    payload = json.dumps(HISTORY)
                conn.send("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n")
                conn.send(payload.encode())
                conn.close(); continue
            
            if path.startswith("/led"):
                # format led?on=1
                resp = "ERR"
                try:
                    # call rpc('led', value) if query present
                    qpos = path.find('?')
                    if qpos != -1:
                        qs = path[qpos+1:]
                        if qs.startswith("on="):
                            val = qs[3:]
                            resp = rpc('led', val)
                        else:
                            resp = rpc('led')
                    else:
                        resp = rpc('led')
                except:
                    resp = "ERR"
                conn.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
                conn.send(str(resp))
                conn.close(); continue
            # phi endpoint: /phi?n=1000
            if path.startswith("/phi"):
                # parse n
                try:
                    qpos = path.find('?')
                    if qpos == -1:
                        conn.send("HTTP/1.1 400 Bad Request\r\n\r\n")
                        conn.close(); continue
                    qs = path[qpos+1:]
                    n = 0
                    for p in qs.split('&'):
                        k_v = p.split('=')
                        if k_v[0] == 'n' and len(k_v) > 1:
                            n = int(k_v[1])
                    
                    reqid1 = rpcReqS('phi', n, 0, n//3)
                    reqid2 = rpcReqS('phi', n, n//3, 2*n//3)
                    # compute local piece
                    def calcPhi(n, start, stop):
                        dx = 1.0 / n
                        phisum = 0.0
                        for i in range(start, stop):
                            x = i * dx
                            phisum += math.sqrt(1.026 + 4*x - x*x)
                        return phisum * dx
                    start = 2*n//3
                    phi_local = float(calcPhi(n, start, n))
                    # wait responses
                    res1 = rpcResW(reqid1, timeout=2000)
                    res2 = rpcResW(reqid2, timeout=2000)
                    try:
                        res1f = float(res1) if res1 is not None else 0.0
                        res2f = float(res2) if res2 is not None else 0.0
                        phi_total = phi_local + res1f + res2f
                        conn.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
                        conn.send(("{:0.8f}".format(phi_total)).encode())
                    except:
                        conn.send("HTTP/1.1 500 Internal Server Error\r\n\r\n")
                except Exception as e:
                    conn.send("HTTP/1.1 500 Internal Server Error\r\n\r\n")
                conn.close(); continue
            # static files (permitir image served locally si lo subiste)
            lp = path.lstrip('/')
            if lp and ".." not in lp:
                content = read_file(lp)
                if content is not None:
                    ctype = "text/plain"
                    if lp.endswith(".html"): ctype = "text/html"
                    if lp.endswith(".css"): ctype = "text/css"
                    conn.send("HTTP/1.1 200 OK\r\nContent-Type: %s\r\n\r\n" % ctype)
                    conn.sendall(content.encode())
                    conn.close(); continue
            # not found
            conn.send("HTTP/1.1 404 Not Found\r\n\r\nNot Found")
            conn.close()
        except Exception as e:
            try:
                conn.close()
            except:
                pass
            print("ERROR serveWeb:", e)
            continue

def setup():
    setupUART()
    configurar_wifi()
    adc = configurar_adc()
    return adc

def main():
    adc = setup()
    # start rpcTask thread
    _thread.start_new_thread(rpcTask, (None,))
    # start sensor thread
    _thread.start_new_thread(thread_sensor, (adc, MQ2))
    # main thread -> serve web
    serveWeb()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import sys
        sys.print_exception(e)
