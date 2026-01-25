import google.generativeai as genai
import json
import logging
import random

logger = logging.getLogger(__name__)

# SUA CHAVE API (Mantenha segura!)
API_KEY = "AIzaSyANFKk7lKLAxTzYOKEAa7-8OV98ipy5jVo" 

def gerar_questao_ia(disciplina, topico, habilidade, dificuldade):
    try:
        genai.configure(api_key=API_KEY)
        
        # Detecção de modelos (Mantida igual)
        model_name = None
        try:
            modelos_disponiveis = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            preferencias = ['models/gemini-1.5-flash', 'models/gemini-pro', 'models/gemini-1.0-pro']
            for pref in preferencias:
                if pref in modelos_disponiveis:
                    model_name = pref
                    break
            if not model_name and modelos_disponiveis:
                model_name = modelos_disponiveis[0]
        except:
            model_name = 'gemini-pro'

        if not model_name: return {"erro": "Nenhum modelo disponível."}

        model = genai.GenerativeModel(model_name)

        # --- AQUI ESTÁ A MUDANÇA NO PROMPT ---
        prompt = f"""
        Atue como um professor especialista no ENEM. Crie uma questão de múltipla escolha INÉDITA.
        
        CONTEXTO:
        - Disciplina: {disciplina}
        - Tópico: {topico}
        - Habilidade: {habilidade}
        - Nível: {dificuldade}
        
        REGRAS OBRIGATÓRIAS:
        1. O Enunciado deve ser contextualizado (situação-problema).
        2. Crie 5 alternativas (A, B, C, D, E).
        3. IMPORTANTE: A resposta correta (gabarito) DEVE ser escolhida aleatoriamente entre A, B, C, D ou E. NÃO COLOQUE SEMPRE NA 'A'.
        4. O JSON de resposta deve indicar qual letra é a correta.
        
        SAÍDA (JSON Puro):
        {{
            "enunciado": "...",
            "A": "...",
            "B": "...",
            "C": "...",
            "D": "...",
            "E": "...",
            "gabarito": "C", 
            "descritor_sugerido": "{habilidade}"
        }}
        """
        # (Note que mudei o exemplo do gabarito para 'C' para forçar a variação)

        response = model.generate_content(prompt)
        texto_limpo = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(texto_limpo)

    except Exception as e:
        logger.error(f"Erro na IA: {str(e)}")
        return {"erro": f"Falha técnica ({str(e)})"}