"""Проверка связи с Discord: DNS, подключение по IPv4/IPv6, TLS и запрос к API."""
import socket
import ssl
import time
import urllib.request

HOSTS = ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"]


def check_host(host):
    print(f"\n== {host} ==")
    try:
        infos = socket.getaddrinfo(host, 443, 0, socket.SOCK_STREAM)
    except Exception as e:
        print(f"  DNS не работает: {e}")
        return {"dns": False, "v4": False, "v6": False}
    result = {"dns": True, "v4": False, "v6": False}
    seen = set()
    for family, _, _, _, addr in infos:
        ip = addr[0]
        if ip in seen:
            continue
        seen.add(ip)
        version = "IPv6" if family == socket.AF_INET6 else "IPv4"
        started = time.time()
        try:
            with socket.create_connection((ip, 443), timeout=6) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                    pass
            print(f"  {version} {ip}: подключение и TLS работают ({time.time() - started:.1f} с)")
            result["v6" if version == "IPv6" else "v4"] = True
        except Exception as e:
            print(f"  {version} {ip}: НЕ РАБОТАЕТ ({type(e).__name__}: {e})")
    return result


def check_api():
    print("\n== Запрос к API Discord ==")
    try:
        with urllib.request.urlopen("https://discord.com/api/v10/gateway", timeout=8) as resp:
            print(f"  ответ {resp.status}: {resp.read(80).decode('utf-8', 'replace')}")
        return True
    except Exception as e:
        print(f"  НЕ РАБОТАЕТ ({type(e).__name__}: {e})")
        return False


def main():
    results = {h: check_host(h) for h in HOSTS}
    api = check_api()
    print("\n========== ВЫВОД ==========")
    if all(r["v4"] for r in results.values()) and api:
        print("Связь с Discord по IPv4 работает: бот должен подключиться. Если не подключается, пришлите этот текст.")
    elif any(r["v4"] for r in results.values()):
        print("Часть адресов Discord доступна, часть нет. Скорее всего, программа обхода блокировок "
              "действует не на все адреса или не на все программы. Проверьте её настройки (нужен режим для всех программ).")
    else:
        print("Python не может подключиться к Discord, даже если приложение Discord работает. "
              "Программа обхода, скорее всего, обрабатывает только Discord и браузер. Нужен обход для всех программ, "
              "VPN на весь компьютер или прокси (PROXY_URL в .env).")
    if any(r["v6"] for r in results.values()) and not any(r["v4"] for r in results.values()):
        print("Работает только IPv6: добавьте в .env строку FORCE_IPV4=0.")


if __name__ == "__main__":
    main()
