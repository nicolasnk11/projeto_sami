import cv2
import numpy as np
from imutils import contours as im_contours
from pyzbar.pyzbar import decode

class OMRScanner:
    def processar_cartao(self, image_path, qtd_questoes=10, alternativas=5):
        print(f"--- LEITURA (LAYOUT QR EMBAIXO): {image_path} ---")
        
        image = cv2.imread(image_path)
        if image is None: return {"sucesso": False, "erro": "Imagem inválida"}
        
        # 1. Tenta ler o QR Code na imagem cheia
        dados_qr = None
        decoded = decode(image)
        if decoded:
            dados_qr = decoded[0].data.decode("utf-8")
            print(f"✅ QR Code Detectado: {dados_qr}")

        # 2. TRATAMENTO PARA ACHAR BOLINHAS
        # Como o QR Code está embaixo, as bolinhas estão acima dele.
        # Vamos focar no CENTRO da imagem, ignorando o rodapé (onde está o QR)
        
        height, width = image.shape[:2]
        
        # CORTA O RODAPÉ (Ignora os últimos 20% da imagem para tirar o QR Code da visão)
        # Isso evita que o scanner ache que o QR Code é um monte de bolinhas quadradas
        imagem_sem_rodape = image[0:int(height*0.80), :] 
        
        # Agora sim, redimensiona para processar
        h_c, w_c = imagem_sem_rodape.shape[:2]
        if w_c > 1200:
            scale = 1200 / float(w_c)
            imagem_processar = cv2.resize(imagem_sem_rodape, (1200, int(h_c * scale)))
        else:
            imagem_processar = imagem_sem_rodape

        gray = cv2.cvtColor(imagem_processar, cv2.COLOR_BGR2GRAY)
        
        # Binarização (Preto e Branco)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 41, 10)

        # 3. BUSCA BOLINHAS
        cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts[0] if len(cnts) == 2 else cnts[1]
        
        question_cnts = []
        for c in cnts:
            (x, y, w, h) = cv2.boundingRect(c)
            ar = w / float(h)
            
            # Filtro para bolinhas (ajustado para seu PDF)
            if w >= 14 and h >= 14 and w <= 80 and h <= 80 and ar >= 0.6 and ar <= 1.4:
                question_cnts.append(c)

        print(f"👀 Bolinhas encontradas (sem rodapé): {len(question_cnts)}")
        
        if not question_cnts:
             return {"sucesso": False, "erro": "Não achei bolinhas.", "qr_code": dados_qr}

        # 4. ORDENAR E LER
        try:
            # Ordena de Cima para Baixo
            question_cnts = im_contours.sort_contours(question_cnts, method="top-to-bottom")[0]
            
            respostas_lidas = {}
            questoes_validas = []
            
            # Agrupa linhas (Eixo Y)
            if len(question_cnts) > 0:
                linha_atual = [question_cnts[0]]
                for i in range(1, len(question_cnts)):
                    c_atual = question_cnts[i]
                    c_anterior = question_cnts[i-1]
                    (_, y_a, _, _) = cv2.boundingRect(c_atual)
                    (_, y_ant, _, _) = cv2.boundingRect(c_anterior)
                    
                    if abs(y_a - y_ant) < 20: # Mesma linha
                        linha_atual.append(c_atual)
                    else:
                        linha_atual = im_contours.sort_contours(linha_atual, method="left-to-right")[0]
                        questoes_validas.append(linha_atual)
                        linha_atual = [c_atual]
                if linha_atual:
                    linha_atual = im_contours.sort_contours(linha_atual, method="left-to-right")[0]
                    questoes_validas.append(linha_atual)
            
            # Lê respostas
            q_num = 1
            for cnts_linha in questoes_validas:
                if len(cnts_linha) >= alternativas:
                    # Pega as 5 primeiras
                    cnts_linha = cnts_linha[:alternativas]
                    
                    bubbled = None
                    max_pixels = 0
                    
                    for (j, c) in enumerate(cnts_linha):
                        mask = np.zeros(thresh.shape, dtype="uint8")
                        cv2.drawContours(mask, [c], -1, 255, -1)
                        mask = cv2.bitwise_and(thresh, thresh, mask=mask)
                        total = cv2.countNonZero(mask)
                        
                        if total > max_pixels:
                            max_pixels = total
                            bubbled = j
                    
                    # Limiar de tinta (100px)
                    if bubbled is not None and max_pixels > 100:
                        letra = ['A', 'B', 'C', 'D', 'E'][bubbled]
                        respostas_lidas[q_num] = letra
                        print(f"   ✅ Q{q_num}: {letra}")
                    
                    q_num += 1

            return {
                "sucesso": True, 
                "respostas": respostas_lidas,
                "qr_code": dados_qr
            }

        except Exception as e:
            print(f"❌ Erro: {e}")
            return {"sucesso": False, "erro": str(e), "qr_code": dados_qr}