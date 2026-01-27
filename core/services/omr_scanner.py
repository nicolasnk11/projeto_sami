import cv2
import numpy as np
from imutils import contours as im_contours
from pyzbar.pyzbar import decode

class OMRScanner:
    def processar_cartao(self, image_path, qtd_questoes=10, alternativas=5):
        print(f"--- LEITURA FINAL: {image_path} ---")
        
        image = cv2.imread(image_path)
        if image is None: return {"sucesso": False, "erro": "Imagem inválida"}
        
        # 1. QR CODE (Lê antes de mexer na imagem)
        dados_qr = None
        decoded = decode(image)
        if decoded:
            dados_qr = decoded[0].data.decode("utf-8")
            print(f"✅ QR Code: {dados_qr}")

        # 2. CORRETIVO DIGITAL (Para apagar o QR Code)
        h_orig, w_orig = image.shape[:2]
        
        # AJUSTE 1: Pinta só os 12% finais da altura (0.88)
        # E começa um pouco depois do meio (0.6) para garantir que não pegue a coluna 1
        ponto_inicial = (int(w_orig * 0.60), int(h_orig * 0.88))
        ponto_final = (w_orig, h_orig)
        cv2.rectangle(image, ponto_inicial, ponto_final, (255, 255, 255), -1)
        
        # Redimensiona
        target_w = 1200
        if w_orig > target_w:
            scale = target_w / float(w_orig)
            image = cv2.resize(image, (target_w, int(h_orig * scale)))

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # AJUSTE 2: Threshold mais forte (51) para lidar com sombras no rodapé
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 51, 15)

        # 3. ACHAR BOLINHAS
        cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]
        
        todas_bolinhas = []
        for c in cnts:
            (x, y, wb, hb) = cv2.boundingRect(c)
            ar = wb / float(hb)
            if wb >= 14 and hb >= 14 and wb <= 80 and hb <= 80 and ar >= 0.55 and ar <= 1.45:
                todas_bolinhas.append(c)

        if not todas_bolinhas:
             return {"sucesso": False, "erro": "Não achei bolinhas.", "qr_code": dados_qr}

        print(f"👀 Total Bolinhas: {len(todas_bolinhas)}")

        # 4. DIVISÃO COLUNAS
        coords_x = [cv2.boundingRect(c)[0] for c in todas_bolinhas]
        min_x = min(coords_x)
        max_x = max(coords_x)
        largura = max_x - min_x
        
        coluna_esq = []
        coluna_dir = []

        if largura > 250:
            divisor = (min_x + max_x) // 2
            for c in todas_bolinhas:
                x = cv2.boundingRect(c)[0]
                if x < divisor: coluna_esq.append(c)
                else: coluna_dir.append(c)
        else:
            coluna_esq = todas_bolinhas

        print(f"📊 Distribuição: {len(coluna_esq)} Esq | {len(coluna_dir)} Dir")

        # 5. LEITURA
        respostas_lidas = {}
        
        def ler_bloco(bolinhas, num_inicial):
            if not bolinhas: return num_inicial
            
            bolinhas = im_contours.sort_contours(bolinhas, method="top-to-bottom")[0]
            
            linhas = []
            linha_atual = [bolinhas[0]]
            for i in range(1, len(bolinhas)):
                c_atual = bolinhas[i]
                c_ant = bolinhas[i-1]
                (_, y_a, _, _) = cv2.boundingRect(c_atual)
                (_, y_ant, _, _) = cv2.boundingRect(c_ant)
                
                if abs(y_a - y_ant) < 20:
                    linha_atual.append(c_atual)
                else:
                    linha_atual = im_contours.sort_contours(linha_atual, method="left-to-right")[0]
                    linhas.append(linha_atual)
                    linha_atual = [c_atual]
            if linha_atual:
                linha_atual = im_contours.sort_contours(linha_atual, method="left-to-right")[0]
                linhas.append(linha_atual)
            
            q_local = num_inicial
            for l in linhas:
                # AJUSTE 3: Aceita linhas com 3 bolinhas (tolerância maior para rodapé ruim)
                if len(l) >= 3:
                    # Tenta pegar até 5, mas se tiver menos, usa o que tem
                    l_sort = l[:alternativas]
                    
                    max_px = 0
                    bubbled = None
                    for j, c in enumerate(l_sort):
                        mask = np.zeros(thresh.shape, dtype="uint8")
                        cv2.drawContours(mask, [c], -1, 255, -1)
                        mask = cv2.bitwise_and(thresh, thresh, mask=mask)
                        total = cv2.countNonZero(mask)
                        if total > max_px:
                            max_px = total
                            bubbled = j
                    
                    if bubbled is not None and max_px > 100:
                        letra = ['A', 'B', 'C', 'D', 'E'][bubbled]
                        respostas_lidas[q_local] = letra
                        print(f"   ✅ Q{q_local}: {letra}")
                    else:
                        # Se achou a linha mas não viu tinta suficiente
                        print(f"   ⚪ Q{q_local}: Em branco (Máx {max_px}px)")
                        
                    q_local += 1
                else:
                    print(f"   ⚠️ Linha ignorada (apenas {len(l)} bolinhas)")
            return q_local

        ler_bloco(coluna_esq, 1)
        
        start_dir = 16 
        if coluna_dir:
            ler_bloco(coluna_dir, start_dir)

        return {
            "sucesso": True, 
            "respostas": respostas_lidas,
            "qr_code": dados_qr
        }