import cv2
import numpy as np
import os
from pyzbar.pyzbar import decode

class OMRScanner:
    def __init__(self, debug_mode=True):
        self.debug = debug_mode
        self.debug_dir = "media/temp/debug"
        if self.debug:
            os.makedirs(self.debug_dir, exist_ok=True)

    def _salvar_debug(self, nome, img):
        if self.debug:
            cv2.imwrite(os.path.join(self.debug_dir, nome), img)

    def _ordenar_pontos(self, pts):
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    def _corrigir_perspectiva(self, image, pts):
        rect = self._ordenar_pontos(pts)
        (tl, tr, br, bl) = rect
        
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))
        
        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))
        
        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")
        
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

    def processar_cartao(self, image_path, qtd_questoes=30, alternativas=5, threshold=0.45):
        print(f"🚀 OMR PRODUCTION SCALED - {image_path}")
        
        image = cv2.imread(image_path)
        if image is None: return {"sucesso": False, "erro": "Imagem inválida"}

        # 1. QR Code
        dados_qr = None
        try:
            decodes = decode(image)
            if decodes:
                dados_qr = decodes[0].data.decode("utf-8")
        except Exception:
            pass

        # 2. Resize Controlado e Fator de Escala Dinâmico
        h, w = image.shape[:2]
        target_h = 1200
        scale_factor = target_h / float(h)
        image = cv2.resize(image, (int(w * scale_factor), target_h))

        # 3. CLAHE (Correção de Iluminação)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray = clahe.apply(gray)
        
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 75, 200)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)

        # Fix de Compatibilidade OpenCV 3 vs 4
        cnts_info = cv2.findContours(edges.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts_info[0] if len(cnts_info) == 2 else cnts_info[1]
        
        docCnt = None
        if len(cnts) > 0:
            cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
            for c in cnts:
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                # Área de detecção da folha baseada na escala
                if len(approx) == 4 and cv2.contourArea(c) > (60000 * scale_factor):
                    docCnt = approx
                    break

        if docCnt is not None:
            warped = self._corrigir_perspectiva(image, docCnt.reshape(4, 2))
            warped_gray = clahe.apply(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY))
        else:
            warped, warped_gray = image, gray

        self._salvar_debug("01_warped.jpg", warped)

        # 4. Adaptive Threshold
        thresh = cv2.adaptiveThreshold(warped_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4)
        self._salvar_debug("02_thresh.jpg", thresh)

        # 5. Encontrar Bolinhas com Filtros Escalonados
        cnts_info = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = cnts_info[0] if len(cnts_info) == 2 else cnts_info[1]
        
        todas_bolinhas = []
        img_bolinhas = warped.copy()

        # Tamanhos baseados no Scale Factor
        min_size = int(18 * scale_factor)
        max_size = int(50 * scale_factor)

        for c in cnts:
            x, y, w_box, h_box = cv2.boundingRect(c)
            ar = w_box / float(h_box)
            area = cv2.contourArea(c)
            
            # Filtro Dinâmico
            if min_size <= w_box <= max_size and min_size <= h_box <= max_size and 0.75 <= ar <= 1.25:
                extent = area / float(w_box * h_box)
                # Adicionamos o scale_factor no y do cabeçalho!
                if 0.55 <= extent <= 0.95 and y > int(150 * scale_factor): 
                    todas_bolinhas.append(c)
                    cv2.rectangle(img_bolinhas, (x, y), (x+w_box, y+h_box), (255, 0, 0), 2)

        self._salvar_debug("03_bolinhas.jpg", img_bolinhas)

        if not todas_bolinhas:
            return {"sucesso": False, "erro": "Nenhuma marcação detectável.", "qr_code": dados_qr}

        # 6. Dividir Colunas com Gap Escalonado
        coords_x = [cv2.boundingRect(c)[0] for c in todas_bolinhas]
        coords_x_sorted = sorted(coords_x)
        gaps = [coords_x_sorted[i+1] - coords_x_sorted[i] for i in range(len(coords_x_sorted)-1)]
        
        colunas = []
        gap_limit = 60 * scale_factor # Gap dinâmico
        
        if gaps and max(gaps) > gap_limit:
            gap_idx = np.argmax(gaps)
            split_val = coords_x_sorted[gap_idx] + (max(gaps) / 2)
            
            col_esq = [c for c in todas_bolinhas if cv2.boundingRect(c)[0] < split_val]
            col_dir = [c for c in todas_bolinhas if cv2.boundingRect(c)[0] > split_val]
            
            colunas = [col_esq, col_dir] if len(col_dir) > 3 else [todas_bolinhas]
        else:
            colunas = [todas_bolinhas]

        # 7. Leitura Final com Erosão
        respostas_lidas = {}
        img_resultados = warped.copy()
        prox_num = 1

        for col in colunas:
            if not col: continue
            col = sorted(col, key=lambda c: cv2.boundingRect(c)[1])
            
            linhas = []
            linha_atual = [col[0]]
            # Tolerância de alinhamento escalonada
            y_tolerance = 25 * scale_factor 
            
            for i in range(1, len(col)):
                if abs(cv2.boundingRect(col[i])[1] - cv2.boundingRect(linha_atual[-1])[1]) < y_tolerance:
                    linha_atual.append(col[i])
                else:
                    if len(linha_atual) >= 3: 
                        linhas.append(sorted(linha_atual, key=lambda c: cv2.boundingRect(c)[0]))
                    linha_atual = [col[i]]
            if len(linha_atual) >= 3:
                linhas.append(sorted(linha_atual, key=lambda c: cv2.boundingRect(c)[0]))

            for linha in linhas:
                l_sort = linha[:alternativas]
                preenchimentos = []

                for j, c in enumerate(l_sort):
                    mask = np.zeros(thresh.shape, dtype="uint8")
                    cv2.drawContours(mask, [c], -1, 255, -1)
                    
                    kernel_erode = np.ones((4,4), np.uint8)
                    mask = cv2.erode(mask, kernel_erode, iterations=1)
                    
                    miolo = cv2.bitwise_and(thresh, thresh, mask=mask)
                    # No THRESH_BINARY_INV a tinta fica branca (255). countNonZero conta brancos.
                    pixels_tinta = cv2.countNonZero(miolo) 
                    area_mascara = cv2.countNonZero(mask)
                    
                    pct = pixels_tinta / float(area_mascara) if area_mascara > 0 else 0
                    preenchimentos.append((pct, j))

                preenchimentos.sort(reverse=True, key=lambda x: x[0])
                
                if preenchimentos[0][0] > threshold:
                    # Dupla marcação
                    if len(preenchimentos) > 1 and preenchimentos[1][0] > threshold:
                        respostas_lidas[prox_num] = "NULA"
                    else:
                        idx = preenchimentos[0][1]
                        respostas_lidas[prox_num] = ['A','B','C','D','E'][idx]
                        
                        bx,by,bw,bh = cv2.boundingRect(l_sort[idx])
                        cv2.rectangle(img_resultados, (bx,by), (bx+bw,by+bh), (0,255,0), 3)
                        
                prox_num += 1

        self._salvar_debug("04_final.jpg", img_resultados)
        return {"sucesso": True, "respostas": respostas_lidas, "qr_code": dados_qr}