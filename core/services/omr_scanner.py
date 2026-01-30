import cv2
import numpy as np
from imutils import contours as im_contours
from pyzbar.pyzbar import decode

class OMRScanner:
    def processar_cartao(self, image_path, qtd_questoes=10, alternativas=5):
        print(f"--- LEITURA INICIADA (Python 3.12): {image_path} ---")
        
        image = cv2.imread(image_path)
        if image is None: 
            return {"sucesso": False, "erro": "Imagem inválida ou não encontrada."}
        
        # 1. QR CODE
        dados_qr = None
        try:
            decoded = decode(image)
            if decoded:
                dados_qr = decoded[0].data.decode("utf-8")
                print(f"✅ QR Code: {dados_qr}")
        except Exception as e:
            print(f"⚠️ Erro QR: {e}")

        # 2. PRÉ-PROCESSAMENTO
        h, w = image.shape[:2]
        # Pinta rodapé de branco para limpar sujeira
        cv2.rectangle(image, (int(w*0.6), int(h*0.88)), (w, h), (255, 255, 255), -1)
        
        target_w = 1200
        if w > target_w:
            scale = target_w / float(w)
            image = cv2.resize(image, (target_w, int(h * scale)))

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 51, 15)

        # 3. DETECTAR BOLINHAS
        cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]
        
        todas_bolinhas = []
        for c in cnts:
            (x, y, wb, hb) = cv2.boundingRect(c)
            ar = wb / float(hb)
            if wb >= 14 and hb >= 14 and wb <= 80 and hb <= 80 and ar >= 0.55 and ar <= 1.45:
                todas_bolinhas.append(c)

        if not todas_bolinhas:
             return {"sucesso": False, "erro": "Nenhuma bolinha encontrada.", "qr_code": dados_qr}

        print(f"👀 Bolinhas encontradas: {len(todas_bolinhas)}")

        # 4. COLUNAS
        coords_x = [cv2.boundingRect(c)[0] for c in todas_bolinhas]
        min_x, max_x = min(coords_x), max(coords_x)
        largura = max_x - min_x
        
        coluna_esq, coluna_dir = [], []
        if largura > 250:
            divisor = (min_x + max_x) // 2
            for c in todas_bolinhas:
                if cv2.boundingRect(c)[0] < divisor: coluna_esq.append(c)
                else: coluna_dir.append(c)
        else:
            coluna_esq = todas_bolinhas

        # 5. LEITURA
        respostas_lidas = {}
        
        def ler_bloco(bolinhas, num_inicial):
            if not bolinhas: return num_inicial
            bolinhas = im_contours.sort_contours(bolinhas, method="top-to-bottom")[0]
            
            linhas = []
            linha_atual = [bolinhas[0]]
            for i in range(1, len(bolinhas)):
                # Se estiver na mesma altura (y próximo)
                if abs(cv2.boundingRect(bolinhas[i])[1] - cv2.boundingRect(bolinhas[i-1])[1]) < 20:
                    linha_atual.append(bolinhas[i])
                else:
                    linha_atual = im_contours.sort_contours(linha_atual, method="left-to-right")[0]
                    linhas.append(linha_atual)
                    linha_atual = [bolinhas[i]]
            if linha_atual:
                linhas.append(im_contours.sort_contours(linha_atual, method="left-to-right")[0])
            
            q_local = num_inicial
            for l in linhas:
                if len(l) >= 3:
                    l_sort = l[:alternativas]
                    max_px, bubbled = 0, None
                    for j, c in enumerate(l_sort):
                        mask = np.zeros(thresh.shape, dtype="uint8")
                        cv2.drawContours(mask, [c], -1, 255, -1)
                        mask = cv2.bitwise_and(thresh, thresh, mask=mask)
                        total = cv2.countNonZero(mask)
                        if total > max_px: max_px, bubbled = total, j
                    
                    if bubbled is not None and max_px > 100:
                        respostas_lidas[q_local] = ['A','B','C','D','E'][bubbled]
                    q_local += 1
            return q_local

        ler_bloco(coluna_esq, 1)
        if coluna_dir: ler_bloco(coluna_dir, 16) # Ajuste o 16 se sua prova tiver numeração diferente

        return {"sucesso": True, "respostas": respostas_lidas, "qr_code": dados_qr}