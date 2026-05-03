import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import re
import gzip
import socketserver

PORT = int(os.environ.get('PORT', 8765))
IS_CLOUD = os.environ.get('RENDER') is not None or 'PORT' in os.environ

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'status': 'ok',
                'service': 'PostML API',
                'endpoints': ['/scrape-ml', '/rec-ml', '/api/dalle']
            }).encode('utf-8'))
            return

        if self.path.startswith('/proxy-ml?'):
            self.handle_ml_proxy()
        elif self.path.startswith('/scrape-ml?'):
            self.handle_scrape()
        elif self.path.startswith('/rec-ml?'):
            self.handle_rec()
        else:
            if IS_CLOUD:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Not found'}).encode('utf-8'))
            else:
                super().do_GET()

    def do_POST(self):
        if self.path == '/api/dalle':
            self.handle_dalle()
        else:
            self.send_error(404, 'Endpoint nao encontrado')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def handle_dalle(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            body = json.loads(post_data)

            api_key = (body.get('api_key') or '').strip()
            prompt = (body.get('prompt') or '').strip()
            size = body.get('size', '1024x1024')
            quality = body.get('quality', 'standard')

            if not api_key.startswith('sk-'):
                self.send_json({'error': {'message': 'API Key OpenAI invalida'}}, 400)
                return
            if not prompt:
                self.send_json({'error': {'message': 'Prompt vazio'}}, 400)
                return

            if size not in ('1024x1024', '1792x1024', '1024x1792'):
                size = '1024x1024'
            if quality not in ('standard', 'hd'):
                quality = 'standard'

            payload = json.dumps({
                'model': 'dall-e-3',
                'prompt': prompt,
                'n': 1,
                'size': size,
                'quality': quality,
                'response_format': 'url'
            }).encode('utf-8')

            req = urllib.request.Request(
                'https://api.openai.com/v1/images/generations',
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + api_key
                },
                method='POST'
            )

            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    image_url = result['data'][0]['url']
                    revised_prompt = result['data'][0].get('revised_prompt', prompt)
                    print(f'[DALL-E] Imagem gerada')
                    self.send_json({
                        'url': image_url,
                        'revised_prompt': revised_prompt,
                        'cost_usd': 0.08 if quality == 'hd' else 0.04
                    })

            except urllib.error.HTTPError as e:
                error_body = e.read().decode('utf-8', errors='ignore')
                try:
                    err_data = json.loads(error_body)
                    msg = err_data.get('error', {}).get('message', f'Erro {e.code}')
                except:
                    msg = f'Erro HTTP {e.code}'
                print(f'[DALL-E] Erro: {msg}')
                self.send_json({'error': {'message': 'OpenAI: ' + msg}}, e.code)

        except Exception as e:
            print(f'[DALL-E] Excecao: {e}')
            self.send_json({'error': {'message': str(e)}}, 500)

    def handle_scrape(self):
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target_url = params.get('url', [None])[0]
            if not target_url or ('mercadolivre' not in target_url and 'mercadolibre' not in target_url):
                self.send_json({'error': 'URL invalida'}, 400)
                return

            req = urllib.request.Request(target_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'pt-BR,pt;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                try:
                    html = gzip.decompress(raw).decode('utf-8', errors='ignore')
                except:
                    html = raw.decode('utf-8', errors='ignore')

            result = {'url': target_url}

            lds = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
            for ld_raw in lds:
                try:
                    data = json.loads(ld_raw)
                    if isinstance(data, list): data = data[0]
                    if data.get('@type') in ('Product', 'ItemPage') or data.get('name'):
                        if not result.get('titulo') and data.get('name'):
                            result['titulo'] = data['name'].strip()
                        if data.get('offers'):
                            offers = data['offers']
                            if isinstance(offers, list): offers = offers[0]
                            p = offers.get('price', 0)
                            if p and float(p) > 0:
                                result['preco'] = float(p)
                        if not result.get('imagem') and data.get('image'):
                            img = data['image']
                            result['imagem'] = img[0] if isinstance(img, list) else img
                        break
                except:
                    pass

            if not result.get('titulo'):
                m = re.search(r'og:title[^>]+content="([^"]+)"', html)
                if not m: m = re.search(r'<title>([^<]+)</title>', html)
                if m: result['titulo'] = m.group(1).strip()

            imgs = re.findall(r'https://http2\.mlstatic\.com/D_NQ[^"\'> ]+\.(?:jpg|webp|jpeg)', html)
            if imgs and not result.get('imagem'):
                jpg_imgs = [i for i in imgs if i.endswith('.jpg') or 'jpg' in i]
                result['imagem'] = jpg_imgs[0] if jpg_imgs else imgs[0]
            elif imgs:
                best = imgs[0].replace('-OO.webp', '-O.jpg').replace('.webp', '.jpg')
                result['imagem'] = best

            if not result.get('preco'):
                m = re.search(r'"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
                if not m: m = re.search(r'"salePriceAmount"\s*:\s*([0-9]+)', html)
                if not m: m = re.search(r'"originalPrice"\s*:\s*([0-9]+)', html)
                if m:
                    val = float(m.group(1))
                    if val > 0: result['preco'] = val

            self.send_json(result)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def handle_rec(self):
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            cat_url = params.get('url', [None])[0]
            if not cat_url:
                self.send_json({'error': 'URL ausente'}, 400)
                return

            req = urllib.request.Request(cat_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'pt-BR,pt;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                try:
                    html = gzip.decompress(raw).decode('utf-8', errors='ignore')
                except:
                    html = raw.decode('utf-8', errors='ignore')

            name_map = {}
            for m in re.finditer(
                r'"id"\s*:\s*"(MLB\d+)"[^}]{0,30}"type"\s*:\s*"PRODUCT"[^{]{0,500}"name"\s*:\s*"([^"]{10,120})"',
                html
            ):
                name_map[m.group(1)] = m.group(2)

            pattern = (
                r'"price"\s*:\s*([0-9]+(?:\.[0-9]+)?)'
                r'.{0,300}?"permalink"\s*:\s*"(https:\\u002F\\u002Fwww\.mercadolivre\.com\.br\\u002F[^"]+)"'
                r'.{0,300}?"thumbnail"\s*:\s*"([^"]+)"'
            )
            raw_matches = re.findall(pattern, html, re.DOTALL)

            seen_urls = set()
            produtos = []
            for preco_str, permalink_raw, thumb_raw in raw_matches:
                url_prod = permalink_raw.replace('\\u002F', '/').replace('\\u0026', '&').replace('\\u003F', '?')
                thumb = thumb_raw.replace('\\u002F', '/').replace('http://', 'https://').replace('-I.jpg', '-O.jpg')

                if url_prod in seen_urls:
                    continue
                seen_urls.add(url_prod)

                mlb_match = re.search(r'MLB\d+', url_prod)
                if mlb_match and mlb_match.group() in name_map:
                    titulo = name_map[mlb_match.group()]
                else:
                    slug = url_prod.split('/p/')[0].rstrip('/').split('/')[-1]
                    titulo = slug.replace('-', ' ').title()

                produtos.append({
                    'titulo': titulo,
                    'preco': float(preco_str),
                    'imagem': thumb,
                    'url': url_prod
                })
                if len(produtos) >= 10:
                    break

            self.send_json({'produtos': produtos, 'total': len(produtos)})
        except Exception as e:
            print(f'[REC] Erro: {e}')
            self.send_json({'error': str(e), 'produtos': []}, 500)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()
        self.wfile.write(body)

    def handle_ml_proxy(self):
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target_url = params.get('url', [None])[0]
            if not target_url:
                self.send_error(400, 'URL ausente')
                return
            if 'mercadolibre.com' not in target_url and 'mercadolivre.com' not in target_url:
                self.send_error(403, 'URL nao permitida')
                return

            req = urllib.request.Request(target_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'pt-BR,pt;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
            })
            with urllib.request.urlopen(req, timeout=15) as response:
                data = response.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e.code)}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def log_message(self, format, *args):
        msg = args[0] if args else ''
        if '/api/dalle' in msg:
            print(f'[DALL-E] {msg}')
        elif '/proxy-ml' in msg or '/scrape-ml' in msg or '/rec-ml' in msg:
            print(f'[API] {msg}')


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    if not IS_CLOUD:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print()
    print('  ================================================')
    if IS_CLOUD:
        print(f'    PostML API - Cloud (porta {PORT})')
    else:
        print(f'    PostML API - Local (porta {PORT})')
    print('  ================================================')
    print()
    print('  Endpoints:')
    print('    GET  /health')
    print('    GET  /scrape-ml')
    print('    GET  /rec-ml')
    print('    POST /api/dalle')
    print()

    with ThreadedHTTPServer(('0.0.0.0', PORT), ProxyHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n  Servidor encerrado.')
