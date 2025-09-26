# -- coding: utf-8 --
import os
import re
import json
import time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "https://www.tdpwines.com.br"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MettricScraper/1.0)"}

# Pega o final da URL e transforma em um codigo de produto
def slug_from_url(url):
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or "produto"

# Baixa a pagina com request e transforma em um objeto BeautifulSoup
def get_soup(url):
    """
    Baixa a página e lida com o popup de verificação de idade
    """
    session = requests.Session()

    # Primeiro acesso para pegar cookies
    r = session.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    # Simula o clique no botão "Sim"
    cookies = {
        'age_verification': 'yes',  # Cookie que indica verificação de idade
        'PHPSESSID': session.cookies.get('PHPSESSID', ''),  # Mantém a sessão
    }

    # Segundo acesso com os cookies de verificação
    r = session.get(url, headers=HEADERS, cookies=cookies, timeout=20)
    r.raise_for_status()

    # Verifica se ainda existe o mask de idade
    soup = BeautifulSoup(r.text, "lxml")
    mask = soup.find('div', id='mask')

    if mask and 'display: block' in mask.get('style', ''):
        print("Aviso: Máscara de idade ainda presente após tentativa de bypass")

    return soup, r.text

# Pega os dados estruturados do type
def extract_jsonld(soup):
    data = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            # .string pode ser None, usar .text ou get_text() é mais seguro
            content = tag.string or tag.get_text()
            obj = json.loads(content.strip())
            if isinstance(obj, dict):
                data.append(obj)
            elif isinstance(obj, list):
                data.extend(obj)
        except (json.JSONDecodeError, AttributeError):
            pass
    return data

# Acha a type Product dentro do type
def pick_product_ld(jsonlds):
    for obj in jsonlds:
        t = obj.get("@type")
        if t == "Product" or (isinstance(t, list) and "Product" in t):
            return obj
    return None

def text_clean(t):
    return re.sub(r"\s+", " ", t).strip()

# Tenta adivinhar a imagem principal (primeiro procura em meta tags, depois em <img>
def guess_main_image(soup, page_url):
    # tenta primeiro metatag OG
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(page_url, og["content"])
    # depois imagens do produto comuns
    candidates = []
    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        if any(k in src.lower() for k in ["produto", "product", "go-up", "cabernet", "wine", "vinho"]):
            candidates.append(urljoin(page_url, src))
    # escolhe a mais “longa” (heurística para maior resolução)
    candidates = list(dict.fromkeys(candidates))
    if candidates:
        return max(candidates, key=len)
    return None

# Procura os links PDF
def find_pdf_links(soup, page_url):
    pdfs = []
    # links óbvios
    for a in soup.select("a[href]"):
        href = a["href"]
        if href.lower().endswith(".pdf") or "ficha" in a.get_text(" ", strip=True).lower():
            pdfs.append(urljoin(page_url, href))
    return list(dict.fromkeys(pdfs))

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

# Baixa as imagens e os PDFs
def download(url, outpath):
    try:
        with requests.get(url, headers=HEADERS, timeout=30, stream=True) as r:
            r.raise_for_status()
            with open(outpath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
    except requests.exceptions.RequestException as e:
        print(f"Falha no download de {url}: {e}")

# ==============================================================================
# FUNÇÃO CORRIGIDA E MELHORADA
# ==============================================================================
def find_characteristic_value(soup, label):
    """
    Busca o valor de uma característica em 3 níveis: JSON-LD, container específico e busca geral.
    A regex foi ajustada para aceitar espaços como separador.
    """
    # Nível 1: JSON-LD
    jsonlds = extract_jsonld(soup)
    prod_ld = pick_product_ld(jsonlds)
    if prod_ld and "additionalProperty" in prod_ld:
        for prop in prod_ld.get("additionalProperty", []):
            if isinstance(prop, dict) and label.lower() in prop.get("name", "").lower():
                return prop.get("value")

    # Nível 2: Busca em containers específicos de características
    # Lista de possíveis classes de containers
    container_selectors = [
        "div.container-caracteristicas", 
        "div.caracteristicas", 
        "div.product-details", 
        "ul.caracteristicas-bloco"
    ]
    for selector in container_selectors:
        container = soup.select_one(selector)
        if container:
            for item in container.find_all(["div", "p", "li", "span"]):
                item_text = item.get_text(" ", strip=True)
                # <-- CORREÇÃO: Regex ajustada para buscar por um ou mais espaços (\s+) após o rótulo.
                # O acento circunflexo (^) garante que a linha COMECE com o rótulo.
                pattern = re.compile(rf"^{label}\s+(.+)", re.IGNORECASE)
                match = pattern.search(item_text)
                if match:
                    return match.group(1).strip()
    
    # Nível 3: Busca geral na página (fallback)
    # Menos preciso, mas útil se a estrutura do site mudar.
    for tag in soup.find_all(["div", "li", "tr", "p", "span"]):
        text = tag.get_text(" ", strip=True)
        pattern = re.compile(rf"^{label}\s+(.+)", re.IGNORECASE) # <-- CORREÇÃO: Mesma regex aplicada aqui.
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
            
    return None

def build_json_from_page(url):
    soup, raw = get_soup(url)
    jsonlds = extract_jsonld(soup)
    prod_ld = pick_product_ld(jsonlds)

    # código do produto
    codigo = None
    if prod_ld:
        codigo = prod_ld.get("sku") or prod_ld.get("mpn")
    if not codigo:
        codigo = slug_from_url(url)

    # campos base
    titulo = (prod_ld.get("name") if prod_ld and prod_ld.get("name") else
              soup.find("h1").get_text(strip=True) if soup.find("h1") else "Produto")

    # descrição geral
    descricao = None
    if prod_ld and prod_ld.get("description"):
        descricao = text_clean(prod_ld["description"])
    if not descricao:
        desc_selectors = ["#descricao", ".descricao", ".product-description", ".product-single__description"]
        for selector in desc_selectors:
            desc_blk = soup.select_one(selector)
            if desc_blk:
                descricao = text_clean(desc_blk.get_text(" ", strip=True))
                break

    # características por rótulos
    caracteristicas = {
        "produtor": find_characteristic_value(soup, "Produtor"),
        "pais": find_characteristic_value(soup, "País"),
        "regiao": find_characteristic_value(soup, "Região"),
        "tipo": find_characteristic_value(soup, "Tipo"),
        "uva": find_characteristic_value(soup, "Uva"),
        "volume_ml": None,
        "safra": find_characteristic_value(soup, "Safra"),
        "teor_alcoolico_percent": None,
        "temperatura_servico_c": find_characteristic_value(soup, "Temperatura de Serviço"),
        "vinificacao": find_characteristic_value(soup, "Vinificação"),
        "maturacao": find_characteristic_value(soup, "Maturação"),
        "corpo": find_characteristic_value(soup, "Corpo"),
        "potencial_guarda_anos": find_characteristic_value(soup, "Potencial de Guarda"),
        "harmonizacoes": None
    }

    # Tratamento especial para volume
    vol = find_characteristic_value(soup, "Volume")
    if vol:
        m = re.search(r"(\d+)\s*ml", vol.lower())
        caracteristicas["volume_ml"] = int(m.group(1)) if m else vol

    # Tratamento especial para teor alcoólico
    teor = find_characteristic_value(soup, "Teor Alcoólico")
    if teor:
        m = re.search(r"(\d+[.,]?\d*)\s*%?", teor.replace(",", "."))
        caracteristicas["teor_alcoolico_percent"] = float(m.group(1)) if m else teor

    # Tratamento especial para harmonização
    harmon = find_characteristic_value(soup, "Harmoniza") or find_characteristic_value(soup, "Harmonizações")
    if harmon:
        hs = [h.strip(" .") for h in re.split(r",|;|/| e ", harmon) if h.strip()]
        caracteristicas["harmonizacoes"] = hs

    # mídia
    imagem = guess_main_image(soup, url)
    pdfs = find_pdf_links(soup, url)

    # fallback: ficha técnica oficial conhecida
    if not pdfs and "go-up-cabernet-sauvignon" in url:
        pdfs = ["https://goupwines.com.br/wp-content/uploads/2022/02/GO-UP-Cabernet-Sauvignon-Reserva.pdf"]

    data = {
        "codigo_produto": codigo,
        "url": url,
        "titulo": titulo,
        "descricao_geral": descricao,
        "caracteristicas": caracteristicas,
        "midia": {
            "imagem_principal_url": imagem,
            "ficha_tecnica_url": pdfs[0] if pdfs else None
        }
    }
    return data

def save_product_bundle(prod, out_base="tdpwines"):
    codigo = prod["codigo_produto"]
    pasta = os.path.normpath(os.path.join(out_base, codigo))
    ensure_dir(pasta)

    # salva JSON
    with open(os.path.join(pasta, "produto.json"), "w", encoding="utf-8") as f:
        json.dump(prod, f, ensure_ascii=False, indent=2)

    # baixar imagem
    img_url = prod["midia"].get("imagem_principal_url")
    if img_url:
        ext = os.path.splitext(urlparse(img_url).path)[1] or ".jpg"
        download(img_url, os.path.join(pasta, f"imagem_principal{ext}"))
        
    # baixa ficha técnica (PDF)
    pdf_url = prod["midia"].get("ficha_tecnica_url")
    if pdf_url:
        if "drive.google.com" in pdf_url.lower():
            pdf_url = convert_drive_link(pdf_url)
        download(pdf_url, os.path.join(pasta, "ficha_tecnica.pdf"))

    return pasta

def convert_drive_link(link):
    m = re.search(r"/d/([^/]+)", link)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return link

# ---------- CRAWLER DO CATÁLOGO ----------
LISTING_SEEDS = [
    f"{BASE}/pais/chile",
    f"{BASE}/pais",
    f"{BASE}/produtor/goup",
]

def find_product_links(soup, page_url):
    links = set()
    for a in soup.select("a[href]"):
        href = a["href"]
        if any(x in href for x in ["/vinho-", "/produto/", "/go-up", "/goup"]):
            full = urljoin(page_url, href)
            if BASE in full:
                links.add(full.split("?")[0].split("#")[0])
    return links

def crawl_catalog(max_pages=200):
    to_visit = set(LISTING_SEEDS)
    seen = set()
    product_pages = set()
    
    print(f"\n[DEBUG] Iniciando crawl do catálogo...")
    print(f"[DEBUG] Seeds iniciais: {len(LISTING_SEEDS)} URLs")
    
    try:
        while to_visit and len(seen) < max_pages:
            url = to_visit.pop()
            seen.add(url)
            print(f"\n[DEBUG] Visitando página: {url}")
            
            try:
                soup, _ = get_soup(url)
                new_products = find_product_links(soup, url)
                product_pages.update(new_products)
                print(f"[DEBUG] Encontrados {len(new_products)} links de produtos nesta página")
                print(f"[DEBUG] Total de produtos até agora: {len(product_pages)}")
                
                # Encontra links de paginação
                pagination_links = set()
                for a in soup.select("a[href*='pg='], a[href*='page=']"):
                    pagination_links.add(urljoin(url, a["href"]))
                
                to_visit.update(pagination_links - seen)
                print(f"[DEBUG] Novas páginas para visitar: {len(pagination_links)}")
                print(f"[DEBUG] Total na fila: {len(to_visit)}")
                
                time.sleep(1)  # Aumentado para 1 segundo para evitar bloqueio
                
            except requests.exceptions.RequestException as e:
                print(f"[ERRO] Falha ao processar {url}: {e}")
                continue
            
            except Exception as e:
                print(f"[ERRO] Erro inesperado em {url}: {e}")
                continue
    
    except KeyboardInterrupt:
        print("\n[INFO] Processo interrompido pelo usuário. Salvando produtos encontrados até agora...")
    
    product_pages = {u for u in product_pages if re.search(r"/vinho-|/go-up|/produto/", u)}
    return sorted(product_pages)

def run_single(url):
    print(f"Processando URL única: {url}")
    try:
        prod = build_json_from_page(url)
        pasta = save_product_bundle(prod)
        print("Salvo com sucesso em:", pasta)
        # Opcional: imprimir o JSON para verificação rápida
        # print(json.dumps(prod, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Ocorreu um erro ao processar {url}: {e}")


def run_catalog():
    try:
        links = crawl_catalog()
        total_links = len(links)
        print(f"\n[INFO] Encontrados {total_links} produtos para extrair.")
        
        for i, url in enumerate(links, 1):
            print(f"\n[INFO] [{i}/{total_links}] Processando: {url}")
            
            try:
                prod = build_json_from_page(url)
                pasta = save_product_bundle(prod)
                print(f"[SUCESSO] Produto salvo em: {pasta}")
                
                # Pausa variável para evitar bloqueio
                sleep_time = random.uniform(1.0, 2.0)
                time.sleep(sleep_time)
                
            except Exception as e:
                print(f"[ERRO] Falha ao processar produto {url}: {e}")
                continue
    
    except KeyboardInterrupt:
        print("\n[INFO] Processo interrompido pelo usuário.")
    
    finally:
        print("\n[INFO] Processo de extração finalizado.")

if __name__ == "__main__":
    import random  # Adicione no topo do arquivo
    
    # Adicione tratamento de argumentos
    import sys
    
    if len(sys.argv) > 1:
        # Se passar URL como argumento, processa apenas ela
        url_produto = sys.argv[1]
        print(f"[INFO] Processando URL única: {url_produto}")
        run_single(url_produto)
    else:
        # Caso contrário, processa o catálogo todo
        print("[INFO] Iniciando processamento do catálogo completo...")
        run_catalog()