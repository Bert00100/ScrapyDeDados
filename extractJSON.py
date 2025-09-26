import os
import json
import sqlite3

DB_PATH = r"./dados.db"
OUTPUT_DIR = r"./DbJSON"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "db_export.json")

def exportar_json():
    # Garante que a pasta de saída exista
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Conectar no banco
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Buscar até 100 mil conteúdos
    cursor.execute("SELECT content FROM json_data LIMIT 90000 OFFSET 90000;")
    rows = cursor.fetchall()

    dados = []
    for row in rows:
        try:
            # row[0] é texto JSON -> reconverter para dict/list
            dados.append(json.loads(row[0]))
        except Exception as e:
            print(f"[ERRO] Não foi possível carregar JSON: {e}")

    conn.close()

    # Salvar tudo num único JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)

    print(f"Exportação concluída ✅ Arquivo salvo em: {OUTPUT_FILE}")

if __name__ == "__main__":
    exportar_json()
