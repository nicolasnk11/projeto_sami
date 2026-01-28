import io
import os
import json
import csv
import qrcode
import unicodedata
import pandas as pd
from random import shuffle
from datetime import datetime
from io import StringIO, BytesIO

# Django Imports
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Sum, Q, F
from django.http import FileResponse, JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
# ReportLab Imports (PDF)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.graphics import renderPDF
from reportlab.lib.units import cm
from datetime import datetime
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader

# Seus Modelos e Forms
from .models import (
    Turma, Resultado, Avaliacao, Questao, Aluno, Disciplina, 
    RespostaDetalhada, ItemGabarito, Descritor, NDI, PlanoEnsino,
    TopicoPlano, ConfiguracaoSistema, Tutorial, CategoriaAjuda
)
from .forms import (
    AvaliacaoForm, ResultadoForm, GerarProvaForm, ImportarQuestoesForm, 
    DefinirGabaritoForm, ImportarAlunosForm, AlunoForm
)

from reportlab.lib.utils import ImageReader
from .services.ai_generator import gerar_questao_ia
from .services.omr_scanner import OMRScanner

# ==============================================================================
# 🖨️ FUNÇÕES AUXILIARES DE PDF (LAYOUT)
# ==============================================================================

# ==============================================================================
# 🖨️ FUNÇÕES AUXILIARES DE PDF (LAYOUT PREMIUM)
# ==============================================================================

def desenhar_cabecalho_prova(p, titulo, turma_nome, disciplina_nome):
    """Cabeçalho da Prova com Logo e Nome da Escola."""
    config = ConfiguracaoSistema.objects.first()
    
    # Cores e Fontes
    cor_pri = colors.HexColor(config.cor_primaria) if config else colors.black
    nome_escola = config.nome_escola.upper() if config else "ESCOLA MODELO SAMI"
    
    # Borda Externa
    p.setLineWidth(1)
    p.setStrokeColor(cor_pri)
    p.rect(30, 750, 535, 80) # Caixa principal
    
    offset_x = 0
    # Desenha Logo (se existir)
    if config and config.logo:
        try:
            # Tenta carregar imagem
            logo_img = ImageReader(config.logo.path)
            # Desenha no canto esquerdo
            p.drawImage(logo_img, 40, 760, width=60, height=60, mask='auto', preserveAspectRatio=True)
            offset_x = 70 # Empurra o texto
        except:
            pass

    # Nome da Escola (Centralizado considerando o logo)
    centro_x = 297 + (offset_x / 2)
    p.setFillColor(cor_pri)
    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(centro_x, 810, nome_escola)
    
    # Subtítulo (Prova)
    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 10)
    p.drawCentredString(centro_x, 795, f"AVALIAÇÃO DE {disciplina_nome.upper()} - {titulo.upper()}")
    
    # Linhas de Preenchimento
    p.setFont("Helvetica", 10)
    p.drawString(40 + offset_x, 775, "ALUNO(A): __________________________________________________")
    p.drawString(460, 775, "Nº: _______")
    
    p.drawString(40 + offset_x, 758, f"TURMA: {turma_nome}")
    p.drawString(280 + offset_x, 758, "DATA: ____/____/____")
    p.drawString(460, 758, "NOTA: _______")

# ==============================================================================
# 🛠️ MÁQUINA DE LEITURA (EXCEL/CSV)
# ==============================================================================

def ler_planilha_inteligente(arquivo):
    """Lê Excel ou CSV detectando separadores e encoding automaticamente."""
    arquivo.seek(0)
    nome = arquivo.name.lower()
    
    if nome.endswith(('.xls', '.xlsx')):
        return pd.read_excel(arquivo)
    
    # Se for CSV
    conteudo = arquivo.read()
    try:
        texto = conteudo.decode('utf-8-sig')
    except:
        texto = conteudo.decode('latin-1')
        
    primeira_linha = texto.split('\n')[0]
    separador = ';' if primeira_linha.count(';') > primeira_linha.count(',') else ','
    
    return pd.read_csv(io.StringIO(texto), sep=separador)

def normalizar(texto):
    if not isinstance(texto, str): return str(texto)
    return ''.join(c for c in unicodedata.normalize('NFD', texto) 
                   if unicodedata.category(c) != 'Mn').lower().strip()

def achar_coluna(df, possiveis_nomes):
    colunas_reais = list(df.columns)
    colunas_norm = [normalizar(c) for c in colunas_reais]
    
    for alvo in possiveis_nomes:
        alvo_norm = normalizar(alvo)
        if alvo_norm in colunas_norm:
            return colunas_reais[colunas_norm.index(alvo_norm)]
        for i, col_norm in enumerate(colunas_norm):
            if alvo_norm in col_norm:
                return colunas_reais[i]
    return None

def scanner_serie(valor):
    if pd.isna(valor): return 3
    texto = str(valor).upper()
    if '1' in texto: return 1
    if '2' in texto: return 2
    if '3' in texto: return 3
    return 3

def scanner_dificuldade(valor):
    if pd.isna(valor): return 'M'
    texto = str(valor).upper().strip()
    if not texto: return 'M'
    if texto.startswith('F'): return 'F'
    if texto.startswith('D'): return 'D'
    return 'M'

# ==============================================================================
# 📊 DASHBOARD OTIMIZADO 2.0
# ==============================================================================

@login_required
def dashboard(request):
    # --- 1. FILTROS ---
    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    exportar = request.GET.get('export') # Parâmetro para baixar CSV

    # QuerySet Base (Lazy Loading)
    resultados = Resultado.objects.select_related('avaliacao', 'aluno', 'avaliacao__turma', 'avaliacao__disciplina')

    # Aplicação dos Filtros
    if disciplina_id:
        resultados = resultados.filter(avaliacao__disciplina_id=disciplina_id)
    if serie_id:
        resultados = resultados.filter(avaliacao__turma__nome__startswith=serie_id)
    if turma_id:
        resultados = resultados.filter(avaliacao__turma_id=turma_id)
    if aluno_id:
        resultados = resultados.filter(aluno_id=aluno_id)
    if avaliacao_id:
        resultados = resultados.filter(avaliacao_id=avaliacao_id)
    
    # Filtro de Período
    if data_inicio:
        resultados = resultados.filter(avaliacao__data_aplicacao__gte=data_inicio)
    if data_fim:
        resultados = resultados.filter(avaliacao__data_aplicacao__lte=data_fim)

    # --- 2. EXPORTAÇÃO EXCEL (CSV) ---
    if exportar == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="relatorio_sami_{datetime.now().strftime("%Y%m%d")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Data', 'Aluno', 'Turma', 'Avaliação', 'Disciplina', 'Nota (0-10)', 'Situação'])
        
        for res in resultados:
            situacao = "M. Crítico"
            if res.percentual >= 80: situacao = "Adequado"
            elif res.percentual >= 60: situacao = "Intermediário"
            elif res.percentual >= 21: situacao = "Crítico"

            writer.writerow([
                res.avaliacao.data_aplicacao.strftime("%d/%m/%Y"),
                res.aluno.nome_completo,
                res.avaliacao.turma.nome,
                res.avaliacao.titulo,
                res.avaliacao.disciplina.nome if res.avaliacao.disciplina else '-',
                str(round(res.percentual/10, 1)).replace('.', ','),
                situacao
            ])
        return response

    # --- 3. PROCESSAMENTO OTIMIZADO (AGREGAÇÕES) ---
    
    # Busca todas as respostas relevantes de uma vez só
    # Usamos select_related para evitar "N+1 Queries"
    respostas_qs = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related(
        'item_gabarito__descritor', 'questao__descritor', 'item_gabarito', 'questao'
    )

    # A. PROFICIÊNCIA POR DESCRITOR (HABILIDADE)
    # Aqui fazemos um processamento híbrido pois o descritor pode vir de 'item_gabarito' OU 'questao'
    stats_descritores = {}
    
    # Para otimizar, iteramos apenas uma vez sobre as respostas carregadas na memória
    for resp in respostas_qs:
        desc_obj = None
        if resp.item_gabarito and resp.item_gabarito.descritor:
            desc_obj = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor:
            desc_obj = resp.questao.descritor
            
        if desc_obj:
            codigo = desc_obj.codigo
            if codigo not in stats_descritores:
                stats_descritores[codigo] = {'acertos': 0, 'total': 0, 'descricao': desc_obj.descricao}
            
            stats_descritores[codigo]['total'] += 1
            if resp.acertou:
                stats_descritores[codigo]['acertos'] += 1

    # Prepara dados para o Gráfico de Barras
    labels_proficiencia = []
    dados_proficiencia = []
    
    for cod in sorted(stats_descritores.keys()):
        d = stats_descritores[cod]
        perc = (d['acertos'] / d['total']) * 100
        labels_proficiencia.append(cod)
        dados_proficiencia.append(round(perc, 1))

    # B. RANKING DE QUESTÕES (FACIL vs DIFICIL)
    # Agrupa por texto da questão (Enunciado) para identificar padrões
    stats_questoes = {}
    for resp in respostas_qs:
        # Identifica a questão (ID único virtual)
        q_id = f"G{resp.item_gabarito.id}" if resp.item_gabarito else f"Q{resp.questao.id}"
        
        if q_id not in stats_questoes:
            # Tenta pegar texto do descritor ou enunciado
            texto_desc = "Sem descritor"
            texto_enunciado = "..."
            
            if resp.item_gabarito:
                if resp.item_gabarito.descritor: texto_desc = resp.item_gabarito.descritor.codigo
                if resp.item_gabarito.questao_banco: texto_enunciado = resp.item_gabarito.questao_banco.enunciado
            elif resp.questao:
                if resp.questao.descritor: texto_desc = resp.questao.descritor.codigo
                texto_enunciado = resp.questao.enunciado
            
            stats_questoes[q_id] = {
                'desc': texto_desc, 
                'texto': texto_enunciado[:100], 
                'acertos': 0, 
                'total': 0
            }
        
        stats_questoes[q_id]['total'] += 1
        if resp.acertou: stats_questoes[q_id]['acertos'] += 1

    lista_questoes = []
    for k, v in stats_questoes.items():
        perc_acerto = (v['acertos'] / v['total']) * 100
        lista_questoes.append({
            'desc': v['desc'],
            'texto': v['texto'],
            'percentual_acerto': round(perc_acerto, 1),
            'percentual_erro': round(100 - perc_acerto, 1),
            'total': v['total']
        })

    ranking_facil = sorted(lista_questoes, key=lambda x: x['percentual_acerto'], reverse=True)
    ranking_dificil = sorted(lista_questoes, key=lambda x: x['percentual_erro'], reverse=True)

    # C. NÍVEIS DE APRENDIZADO (PIZZA)
    # Usando agregação do banco para ser super rápido
    total_res = resultados.count()
    dados_pizza = [0, 0, 0, 0] # Adequado, Inter, Critico, M. Critico
    detalhes_pizza = {'Adequado': [], 'Intermediário': [], 'Crítico': [], 'Muito Crítico': []}

    # Iteramos sobre os resultados já carregados (select_related ajuda aqui)
    for res in resultados:
        p = float(res.percentual)
        aluno_info = {'nome': res.aluno.nome_completo, 'turma': res.avaliacao.turma.nome, 'nota': round(p, 1)}
        
        if p >= 80: 
            dados_pizza[0] += 1
            detalhes_pizza['Adequado'].append(aluno_info)
        elif p >= 60: 
            dados_pizza[1] += 1
            detalhes_pizza['Intermediário'].append(aluno_info)
        elif p >= 21: 
            dados_pizza[2] += 1
            detalhes_pizza['Crítico'].append(aluno_info)
        else: 
            dados_pizza[3] += 1
            detalhes_pizza['Muito Crítico'].append(aluno_info)

    detalhes_pizza_json = json.dumps(detalhes_pizza)

    # D. GRÁFICO DE EVOLUÇÃO (LINHA)
    labels_evolucao, dados_evolucao = [], []
    
    # Agrupa por avaliação e calcula média direto no banco
    evolucao_qs = resultados.values('avaliacao__titulo', 'avaliacao__data_aplicacao') \
                            .annotate(media=Avg('percentual')) \
                            .order_by('avaliacao__data_aplicacao')
    
    for evo in evolucao_qs:
        labels_evolucao.append(evo['avaliacao__titulo'])
        dados_evolucao.append(round(evo['media'], 1))

    # E. MAPA DE CALOR (HEATMAP) & MAPA DE HABILIDADES
    itens_heatmap = []
    matriz_calor = []
    mapa_habilidades = {} # {Descritor: {'criticas': [nomes], 'adequadas': [nomes]}}

    # Só processa Heatmap se tiver UMA avaliação específica selecionada (senão fica gigante)
    if avaliacao_id:
        obj_avaliacao = Avaliacao.objects.get(id=avaliacao_id)
        itens_heatmap = ItemGabarito.objects.filter(avaliacao=obj_avaliacao).select_related('descritor').order_by('numero')
        
        # Pega resultados ordenados por nome
        resultados_heat = resultados.order_by('aluno__nome_completo')
        
        for res in resultados_heat:
            resps = RespostaDetalhada.objects.filter(resultado=res)
            mapa_res = {r.item_gabarito_id: r.acertou for r in resps}
            
            linha = {'aluno': res.aluno, 'nota': round(res.percentual/10, 1), 'questoes': []}
            
            for item in itens_heatmap:
                status = mapa_res.get(item.id) # True/False/None
                linha['questoes'].append({'acertou': status, 'item': item}) # Passamos o objeto item inteiro para pegar o descritor no template
            
            matriz_calor.append(linha)

        # Mapa de Habilidades (Quem precisa de ajuda em que)
        # Reutiliza o loop de descritores anterior ou faz lógica específica
        # (Para simplificar e não duplicar lógica, vamos usar o calculo global de descritores)
        # * Nota: Implementação simplificada para não estourar complexidade *
        pass 

    # --- 4. CONTEXTO PARA O TEMPLATE ---
    
    # Filtros para o Select
    turmas_filtro = Turma.objects.all()
    alunos_filtro = Aluno.objects.none()
    if serie_id: turmas_filtro = turmas_filtro.filter(nome__startswith=serie_id)
    if turma_id: alunos_filtro = Aluno.objects.filter(turma_id=turma_id)

    # Nome Bonito do Filtro
    nome_filtro = "Visão Geral"
    if avaliacao_id: nome_filtro = f"Prova: {Avaliacao.objects.get(id=avaliacao_id).titulo}"
    elif aluno_id: nome_filtro = f"Aluno: {Aluno.objects.get(id=aluno_id).nome_completo}"
    elif turma_id: nome_filtro = f"Turma: {Turma.objects.get(id=turma_id).nome}"

    contexto = {
        # Filtros
        'serie_selecionada': serie_id,
        'turma_selecionada': turma_id,
        'aluno_selecionado': aluno_id,
        'disciplina_selecionada': disciplina_id,
        'avaliacao_selecionada': avaliacao_id,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        
        # Listas para Selects
        'turmas_da_serie': turmas_filtro,
        'alunos_da_turma': alunos_filtro,
        'disciplinas': Disciplina.objects.all(),
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-data_aplicacao')[:50],
        
        # Dados Gráficos
        'nome_filtro': nome_filtro,
        'total_avaliacoes_contagem': resultados.values('avaliacao').distinct().count(),
        'total_turmas': Turma.objects.count(),
        
        'dados_pizza': dados_pizza,
        'detalhes_pizza_json': detalhes_pizza_json,
        
        'labels_evolucao': labels_evolucao,
        'dados_evolucao': dados_evolucao,
        
        'labels_proficiencia': labels_proficiencia,
        'dados_proficiencia': dados_proficiencia,
        
        'ranking_facil': ranking_facil,
        'ranking_dificil': ranking_dificil,
        
        # Heatmap
        'itens_heatmap': itens_heatmap,
        'matriz_calor': matriz_calor,
        
        # Listagem Histórico
        'ultimos_resultados': resultados.order_by('-id')[:20]
    }

    return render(request, 'core/dashboard.html', contexto)

@login_required
def painel_gestao(request):
    total_turmas = Turma.objects.count()
    total_questoes = Questao.objects.count()
    total_descritores = Descritor.objects.count()
    context = {'total_turmas': total_turmas, 'total_questoes': total_questoes, 'total_descritores': total_descritores}
    return render(request, 'core/painel_gestao.html', context)

# ==============================================================================
# 📥 IMPORTAÇÕES
# ==============================================================================

@login_required
def importar_questoes(request):
    if request.method == 'POST':
        form = ImportarQuestoesForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                arquivo = request.FILES['arquivo_excel']
                df = ler_planilha_inteligente(arquivo)
                
                c_disc = achar_coluna(df, ['disciplina', 'materia'])
                c_enun = achar_coluna(df, ['enunciado', 'questao'])
                c_gab  = achar_coluna(df, ['gabarito', 'resposta'])
                c_serie = achar_coluna(df, ['serie', 'ano'])
                c_dif   = achar_coluna(df, ['dificuldade', 'nivel'])
                c_desc  = achar_coluna(df, ['descritor', 'habilidade'])

                if not (c_disc and c_enun and c_gab):
                    messages.error(request, "Erro: Faltam colunas obrigatórias.")
                    return redirect('importar_questoes')

                criadas = 0
                descritores_novos = 0
                novas_disc = 0
                apelidos = {'portugues': 'Língua Portuguesa', 'matematica': 'Matemática', 
                           'historia': 'História', 'geografia': 'Geografia', 'ciencias': 'Ciências', 
                           'ingles': 'Língua Inglesa', 'biologia': 'Biologia', 'fisica': 'Física', 
                           'quimica': 'Química', 'sociologia': 'Sociologia', 'filosofia': 'Filosofia'}

                for index, row in df.iterrows():
                    try:
                        txt_disc = str(row[c_disc]).strip()
                        nome_limpo = normalizar(txt_disc)
                        nome_final = apelidos.get(nome_limpo, txt_disc)
                        
                        disc_obj, created_disc = Disciplina.objects.get_or_create(
                            nome__iexact=nome_final, defaults={'nome': nome_final}
                        )
                        if created_disc: novas_disc += 1

                        serie_val = scanner_serie(row[c_serie]) if c_serie else 3
                        dif_val = scanner_dificuldade(row[c_dif]) if c_dif else 'M'
                        
                        desc_obj = None
                        if c_desc and pd.notna(row[c_desc]):
                            raw_cod = str(row[c_desc]).replace('-', ' ').strip()
                            cod = raw_cod.split()[0].strip() 
                            desc_obj, created = Descritor.objects.get_or_create(
                                codigo__iexact=cod, disciplina=disc_obj,
                                defaults={'codigo': cod, 'descricao': f'Importado: {raw_cod}', 'tema': 'Geral'}
                            )
                            if created: descritores_novos += 1

                        def get_alt(letra):
                            col = achar_coluna(df, [f'alternativa{letra}', f'opcao{letra}', letra])
                            return str(row[col]) if col and pd.notna(row[col]) else "..."

                        Questao.objects.create(
                            disciplina=disc_obj, serie=serie_val, dificuldade=dif_val, descritor=desc_obj,
                            enunciado=str(row[c_enun]), gabarito=str(row[c_gab]).strip().upper()[0],
                            alternativa_a=get_alt('a'), alternativa_b=get_alt('b'), 
                            alternativa_c=get_alt('c'), alternativa_d=get_alt('d'), alternativa_e=get_alt('e')
                        )
                        criadas += 1
                    except Exception: pass

                msg_extra = f" (+{novas_disc} novas disciplinas e {descritores_novos} descritores)" if novas_disc > 0 else ""
                messages.success(request, f'Sucesso! {criadas} questões importadas{msg_extra}.')
                return redirect('dashboard')
            except Exception as e:
                messages.error(request, f'Erro no arquivo: {str(e)}')
    else:
        form = ImportarQuestoesForm()
    return render(request, 'core/importar_questoes.html', {'form': form})

@login_required
def importar_alunos(request):
    if request.method == 'POST':
        form = ImportarAlunosForm(request.POST, request.FILES) 
        if form.is_valid():
            try:
                arquivo = request.FILES['arquivo_excel']
                df = ler_planilha_inteligente(arquivo)
                
                c_nome = achar_coluna(df, ['nome', 'estudante', 'aluno', 'nome completo'])
                c_turma = achar_coluna(df, ['turma', 'classe', 'serie'])

                if not c_nome:
                    messages.error(request, f"Erro: Não achei a coluna 'Nome'. Colunas lidas: {list(df.columns)}")
                    return redirect('importar_alunos')

                criados = 0
                novas_turmas = 0
                
                for index, row in df.iterrows():
                    try:
                        raw_nome = row[c_nome]
                        if pd.isna(raw_nome) or str(raw_nome).strip() == '': continue
                        nome_aluno = str(raw_nome).strip().upper()

                        turma_obj = None
                        if c_turma and pd.notna(row[c_turma]):
                            nome_turma = str(row[c_turma]).strip()
                            turma_obj, created = Turma.objects.get_or_create(
                                nome=nome_turma, defaults={'ano_letivo': 2026}
                            )
                            if created: novas_turmas += 1
                        else:
                            turma_obj, _ = Turma.objects.get_or_create(nome="SEM TURMA", defaults={'ano_letivo': 2026})

                        Aluno.objects.create(nome_completo=nome_aluno, turma=turma_obj, ativo=True)
                        criados += 1
                    except Exception as e:
                        print(f"Erro ao importar {nome_aluno}: {e}")

                if criados > 0:
                    msg_extra = f" (+{novas_turmas} turmas novas)" if novas_turmas > 0 else ""
                    messages.success(request, f'SUCESSO! {criados} alunos importados{msg_extra}.')
                else:
                    messages.warning(request, 'Nenhum aluno importado. Verifique o terminal.')
                return redirect('dashboard')
            except Exception as e:
                messages.error(request, f'Erro Crítico: {str(e)}')
    else:
        form = ImportarAlunosForm()
    return render(request, 'core/importar_alunos.html', {'form': form})

@login_required
def baixar_modelo(request, formato):
    dados = {
        'Disciplina': ['Matemática', 'Português'], 'Série': ['1', '3'], 'Descritor': ['D12', 'S01'],
        'Dificuldade': ['Fácil', 'Difícil'], 'Enunciado': ['Quanto é 2+2?', 'Sujeito da frase?'],
        'A': ['3', 'Eu'], 'B': ['4', 'Tu'], 'C': ['5', 'Ele'], 'D': ['6', 'Nós'], 'E': ['', ''], 'Gabarito': ['B', 'B']
    }
    df = pd.DataFrame(dados)
    if formato == 'xlsx':
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="modelo_questoes.xlsx"'
        return response
    else:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="modelo_questoes.csv"'
        df.to_csv(path_or_buf=response, sep=';', index=False, encoding='utf-8-sig')
        return response

# ==============================================================================
# 📝 GESTÃO DE AVALIAÇÕES E PROVAS
# ==============================================================================

@login_required
def criar_avaliacao(request):
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        turma_id = request.POST.get('turma')
        disciplina_id = request.POST.get('disciplina')
        data_aplicacao = request.POST.get('data_aplicacao')
        acao = request.POST.get('acao')
        modo = request.POST.get('modo_prova')

        if titulo and turma_id and disciplina_id:
            av = Avaliacao.objects.create(
                titulo=titulo, turma_id=turma_id, disciplina_id=disciplina_id, data_aplicacao=data_aplicacao
            )
            messages.success(request, f'Avaliação "{titulo}" criada com sucesso!')
            if acao == 'salvar_sair': return redirect('gerenciar_avaliacoes')
            elif acao == 'salvar_configurar':
                if modo == 'banco': return redirect('montar_prova', av.id) 
                else: return redirect('definir_gabarito', av.id)
        else:
            messages.error(request, 'Erro: Preencha todos os campos obrigatórios.')
    context = {'turmas': Turma.objects.all(), 'disciplinas': Disciplina.objects.all()}
    return render(request, 'core/criar_avaliacao.html', context)

@login_required
def gerar_prova(request):
    if request.method == 'POST':
        return gerar_prova_pdf(request)
    form = GerarProvaForm()
    return render(request, 'core/configurar_prova.html', {'form': form})

@login_required
def gerar_prova_pdf(request):
    """Gera PDF Híbrido e Salva vínculo com Aluno se for individual."""
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        disciplina_id = request.POST.get('disciplina')
        tipo_foco = request.POST.get('tipo_foco')
        aluno_id = request.POST.get('aluno_id')
        turma_id = request.POST.get('turma_id')
        qtd_questoes = int(request.POST.get('qtd_questoes', 10))
        salvar_sistema = request.POST.get('salvar_sistema') == 'on'

        disciplina_obj = get_object_or_404(Disciplina, id=disciplina_id)
        
        # --- DIAGNÓSTICO ---
        erros_query = RespostaDetalhada.objects.filter(acertou=False, questao__disciplina=disciplina_obj)
        turma_obj = None
        aluno_obj = None # Variável para guardar o aluno foco
        
        if tipo_foco == 'aluno' and aluno_id:
            aluno_obj = Aluno.objects.get(id=aluno_id)
            turma_obj = aluno_obj.turma
            erros_query = erros_query.filter(resultado__aluno_id=aluno_id)
        elif tipo_foco == 'turma' and turma_id:
            turma_obj = Turma.objects.get(id=turma_id)
            erros_query = erros_query.filter(resultado__aluno__turma_id=turma_id)

        if not turma_obj and salvar_sistema:
            turma_obj = Turma.objects.first() 

        # Seleção de descritores
        descritores_criticos = erros_query.values('questao__descritor').annotate(total_erros=Count('id')).order_by('-total_erros')[:5]
        ids_descritores = [item['questao__descritor'] for item in descritores_criticos if item['questao__descritor']]

        # --- SELEÇÃO DE QUESTÕES ---
        questoes_finais = []
        if ids_descritores:
            pool_focado = list(Questao.objects.filter(disciplina=disciplina_obj, descritor__in=ids_descritores))
            shuffle(pool_focado)
            questoes_finais = pool_focado[:qtd_questoes]
        
        falta = qtd_questoes - len(questoes_finais)
        if falta > 0:
            ids_ja_usados = [q.id for q in questoes_finais]
            pool_geral = list(Questao.objects.filter(disciplina=disciplina_obj).exclude(id__in=ids_ja_usados))
            shuffle(pool_geral)
            questoes_finais += pool_geral[:falta]

        shuffle(questoes_finais)
        questoes_selecionadas = questoes_finais

        # --- SALVAR NO BANCO ---
        if salvar_sistema and questoes_selecionadas:
            # CORREÇÃO: Salva o aluno na Avaliação se existir
            nova_avaliacao = Avaliacao.objects.create(
                titulo=f"Reforço: {titulo}", 
                turma=turma_obj, 
                disciplina=disciplina_obj, 
                aluno=aluno_obj, # <--- AQUI O PULO DO GATO
                data_aplicacao=datetime.now().date()
            )
            for i, questao in enumerate(questoes_selecionadas, 1):
                ItemGabarito.objects.create(
                    avaliacao=nova_avaliacao, numero=i, questao_banco=questao,
                    resposta_correta=questao.gabarito, descritor=questao.descritor
                )
            messages.success(request, f"Prova gerada para {aluno_obj.nome_completo if aluno_obj else turma_obj.nome}!")

        # --- PDF DA PROVA ---
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        
        # CORREÇÃO: Cabeçalho Personalizado
        p.setFont("Helvetica-Bold", 14)
        p.drawString(40, 800, f"Avaliação: {titulo}")
        p.setFont("Helvetica", 10)
        
        if aluno_obj:
            # Mostra o nome do Aluno se for individual
            p.drawString(40, 785, f"Aluno: {aluno_obj.nome_completo} | Turma: {turma_obj.nome}")
        else:
            # Mostra só a turma se for geral
            p.drawString(40, 785, f"Turma: {turma_obj.nome if turma_obj else '___'} | Disciplina: {disciplina_obj.nome}")
            
        p.line(40, 775, 550, 775)
        
        y = 750
        # ... (Restante do código de desenho das questões mantém igual) ...
        for i, q in enumerate(questoes_selecionadas, 1):
            p.setFont("Helvetica-Bold", 11)
            texto_completo = f"{i}. {q.enunciado}"
            linhas_enunciado = simpleSplit(texto_completo, "Helvetica-Bold", 11, 480)
            
            espaco_necessario = (len(linhas_enunciado) * 15) + 120 
            if q.imagem: espaco_necessario += 150 

            if y - espaco_necessario < 50:
                p.showPage()
                p.setFont("Helvetica-Bold", 10)
                p.drawString(40, 800, f"Continuação - {titulo}")
                y = 750
            
            for linha in linhas_enunciado:
                p.drawString(40, y, linha)
                y -= 15 

            if q.imagem:
                try:
                    img_path = q.imagem.path
                    img_reader = ImageReader(img_path)
                    iw, ih = img_reader.getSize()
                    aspect = ih / float(iw)
                    display_width = 200
                    display_height = display_width * aspect
                    if y - display_height < 50:
                        p.showPage()
                        y = 750
                    y -= display_height
                    p.drawImage(img_path, 50, y, width=display_width, height=display_height)
                    y -= 10
                except: pass

            p.setFont("Helvetica-Oblique", 8)
            p.setFillColorRGB(0.4, 0.4, 0.4)
            desc_texto = f"Habilidade: {q.descritor.codigo} - {q.descritor.descricao[:60]}..." if q.descritor else "Habilidade: Geral"
            p.drawString(45, y, desc_texto)
            p.setFillColorRGB(0, 0, 0)
            y -= 15

            p.setFont("Helvetica", 10)
            opts = [('a', q.alternativa_a), ('b', q.alternativa_b), ('c', q.alternativa_c), ('d', q.alternativa_d)]
            if q.alternativa_e: opts.append(('e', q.alternativa_e))
            for letra, texto in opts:
                linhas_opt = simpleSplit(f"{letra}) {texto}", "Helvetica", 10, 450)
                for l in linhas_opt:
                    p.drawString(50, y, l)
                    y -= 12
            y -= 15 

        # Gabarito
        p.showPage() 
        p.setFont("Helvetica-Bold", 16)
        p.drawCentredString(300, 800, "GABARITO DO PROFESSOR")
        p.setFont("Helvetica", 10)
        p.drawCentredString(300, 780, f"Prova: {titulo} | Data: {datetime.now().strftime('%d/%m/%Y')}")
        y = 740
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Questão")
        p.drawString(120, y, "Gabarito")
        p.drawString(200, y, "Habilidade")
        p.line(40, y-5, 550, y-5)
        y -= 20
        p.setFont("Helvetica", 10)
        for i, q in enumerate(questoes_selecionadas, 1):
            p.drawString(65, y, str(i).zfill(2))
            p.circle(140, y+3, 8, stroke=1, fill=0) 
            p.drawCentredString(140, y, q.gabarito)
            desc_cod = q.descritor.codigo if q.descritor else "Geral"
            p.drawString(200, y, f"{desc_cod}")
            y -= 20
            if y < 50:
                p.showPage()
                y = 800

        p.save()
        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f'Prova_{titulo}.pdf')

    return redirect('gerenciar_avaliacoes')

@login_required
def baixar_prova_existente(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    # Busca os itens vinculados a essa avaliação
    itens = ItemGabarito.objects.filter(avaliacao=avaliacao, questao_banco__isnull=False).select_related('questao_banco', 'descritor').order_by('numero')

    if not itens.exists():
        messages.error(request, "Esta avaliação não possui questões do banco vinculadas.")
        return redirect('gerenciar_avaliacoes')

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    
    # --- 1. CABEÇALHO (Padrão Escola) ---
    # Vamos usar a função auxiliar que você já tem para manter o padrão bonito
    desenhar_cabecalho_prova(p, avaliacao.titulo, avaliacao.turma.nome, avaliacao.disciplina.nome)
    
    y = 730 # Começa logo abaixo do cabeçalho
    
    for i, item in enumerate(itens, 1):
        q = item.questao_banco
        
        # --- LÓGICA INTELIGENTE DE QUEBRA DE LINHA ---
        p.setFont("Helvetica-Bold", 11)
        texto_completo = f"{item.numero}. {q.enunciado}"
        linhas_enunciado = simpleSplit(texto_completo, "Helvetica-Bold", 11, 480)
        
        # Calcula espaço necessário (Texto + Imagem + Alternativas)
        espaco_necessario = (len(linhas_enunciado) * 15) + 140 
        if q.imagem: espaco_necessario += 150 

        # Verifica se cabe na página, senão cria nova
        if y - espaco_necessario < 50:
            p.showPage()
            # Redesenha cabeçalho simplificado na nova página
            p.setFont("Helvetica-Bold", 10)
            p.drawString(40, 800, f"Continuação - {avaliacao.titulo}")
            p.line(40, 790, 550, 790)
            y = 760
        
        # Desenha Enunciado
        for linha in linhas_enunciado:
            p.drawString(40, y, linha)
            y -= 15 

        # Desenha Imagem (se houver)
        if q.imagem:
            try:
                img_path = q.imagem.path
                img_reader = ImageReader(img_path)
                iw, ih = img_reader.getSize()
                aspect = ih / float(iw)
                display_width = 200
                display_height = display_width * aspect
                
                # Verifica quebra de página só pra imagem
                if y - display_height < 50:
                    p.showPage()
                    y = 760
                
                y -= display_height
                p.drawImage(img_path, 50, y, width=display_width, height=display_height)
                y -= 10
            except: pass

        # Informações de Habilidade (Cinza)
        p.setFont("Helvetica-Oblique", 8)
        p.setFillColorRGB(0.4, 0.4, 0.4)
        
        # Pega descritor do item ou da questão original
        desc = item.descritor if item.descritor else q.descritor
        desc_texto = "Habilidade: Geral"
        if desc:
            desc_texto = f"Habilidade: {desc.codigo} - {desc.descricao[:70]}..."
            
        p.drawString(45, y, desc_texto)
        p.setFillColorRGB(0, 0, 0) # Volta pra preto
        y -= 15

        # Alternativas (Com quebra de linha também!)
        p.setFont("Helvetica", 10)
        opts = [('a', q.alternativa_a), ('b', q.alternativa_b), ('c', q.alternativa_c), ('d', q.alternativa_d)]
        if q.alternativa_e: opts.append(('e', q.alternativa_e))
        
        for letra, texto in opts:
            # Wrap para alternativas longas
            linhas_opt = simpleSplit(f"{letra}) {texto}", "Helvetica", 10, 450)
            for l in linhas_opt:
                p.drawString(50, y, l)
                y -= 12
        
        y -= 15 # Espaço entre questões

    # --- 2. PÁGINA DE GABARITO (IGUAL AO AUTOMÁTICO) ---
    p.showPage() 
    
    # Cabeçalho do Gabarito
    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(300, 800, "GABARITO DO PROFESSOR")
    p.setFont("Helvetica", 10)
    p.drawCentredString(300, 780, f"Prova: {avaliacao.titulo} | Data: {avaliacao.data_aplicacao.strftime('%d/%m/%Y')}")
    
    # Tabela de Respostas
    y = 740
    p.setFont("Helvetica-Bold", 10)
    p.drawString(50, y, "Questão")
    p.drawString(120, y, "Gabarito")
    p.drawString(200, y, "Habilidade / Descritor")
    p.line(40, y-5, 550, y-5)
    y -= 20
    
    p.setFont("Helvetica", 10)
    for item in itens:
        p.drawString(65, y, str(item.numero).zfill(2))
        
        # Círculo no gabarito
        p.circle(140, y+3, 8, stroke=1, fill=0) 
        p.drawCentredString(140, y, item.resposta_correta)
        
        # Descritor
        desc_cod = "Geral"
        desc_item = item.descritor if item.descritor else item.questao_banco.descritor
        if desc_item:
            desc_cod = f"{desc_item.codigo} - {desc_item.tema if desc_item.tema else ''}"
            
        p.drawString(200, y, desc_cod[:50]) # Limita tamanho
        
        y -= 20
        # Se a lista de gabarito for gigante, quebra página
        if y < 50:
            p.showPage()
            y = 800

    p.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'Prova_{avaliacao.titulo}.pdf')

@login_required
def montar_prova(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # Lógica de Salvar (Mantida igual)
    if request.method == 'POST':
        questoes_ids = request.POST.getlist('questoes_selecionadas')
        if questoes_ids:
            ItemGabarito.objects.filter(avaliacao=avaliacao).delete()
            questoes_banco = Questao.objects.filter(id__in=questoes_ids)
            
            for i, questao in enumerate(questoes_banco, 1):
                ItemGabarito.objects.create(
                    avaliacao=avaliacao, numero=i, questao_banco=questao,
                    resposta_correta=questao.gabarito, descritor=questao.descritor
                )
            messages.success(request, f'{len(questoes_ids)} questões vinculadas com sucesso!')
            return redirect('definir_gabarito', avaliacao_id=avaliacao.id)
        else:
            messages.warning(request, "Nenhuma questão foi selecionada.")

    # --- LÓGICA DE FILTROS (NOVA) ---
    questoes = Questao.objects.filter(disciplina=avaliacao.disciplina).order_by('-id')
    
    # Filtros recebidos via GET (URL)
    f_dificuldade = request.GET.get('dificuldade')
    f_serie = request.GET.get('serie')
    f_descritor = request.GET.get('descritor')
    f_busca = request.GET.get('busca')

    if f_dificuldade:
        questoes = questoes.filter(dificuldade=f_dificuldade)
    
    if f_serie:
        questoes = questoes.filter(serie=f_serie)
        
    if f_descritor:
        questoes = questoes.filter(descritor__id=f_descritor)
        
    if f_busca:
        questoes = questoes.filter(enunciado__icontains=f_busca)

    # Dados para popular os selects
    descritores = Descritor.objects.filter(disciplina=avaliacao.disciplina).order_by('codigo')

    context = {
        'avaliacao': avaliacao,
        'questoes': questoes,
        'descritores': descritores,
        # Passar os filtros atuais de volta para o template manter selecionado
        'filtro_dif': f_dificuldade,
        'filtro_serie': f_serie,
        'filtro_desc': int(f_descritor) if f_descritor else None,
        'filtro_busca': f_busca or ''
    }
    
    return render(request, 'core/montar_prova.html', context)

@login_required
def definir_gabarito(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    itens_salvos = ItemGabarito.objects.filter(avaliacao=avaliacao).order_by('numero')
    
    if not itens_salvos.exists() and avaliacao.questoes.exists():
        for i, q in enumerate(avaliacao.questoes.all(), 1):
            # CORREÇÃO: Uso direto do descritor
            ItemGabarito.objects.create(
                avaliacao=avaliacao, numero=i, questao_banco=q,
                resposta_correta=q.gabarito, descritor=q.descritor
            )
        messages.success(request, "Gabarito importado das questões do banco!")
        return redirect('definir_gabarito', avaliacao_id=avaliacao.id)

    if request.method == 'POST':
        if 'qtd_questoes' in request.POST:
            qtd = int(request.POST.get('qtd_questoes'))
            ItemGabarito.objects.filter(avaliacao=avaliacao).delete()
            desc_padrao = Descritor.objects.filter(disciplina=avaliacao.disciplina).first()
            for i in range(1, qtd + 1):
                ItemGabarito.objects.create(
                    avaliacao=avaliacao, numero=i, resposta_correta='A', descritor=desc_padrao
                )
            return redirect('definir_gabarito', avaliacao_id=avaliacao.id)
        else:
            for item in itens_salvos:
                nova_resposta = request.POST.get(f'resposta_{item.id}')
                novo_descritor_id = request.POST.get(f'descritor_{item.id}')
                if nova_resposta: item.resposta_correta = nova_resposta
                if novo_descritor_id: item.descritor_id = novo_descritor_id
                item.save()
            messages.success(request, "Mapeamento salvo!")
            return redirect('gerenciar_avaliacoes')

    context = {
        'avaliacao': avaliacao, 'itens': itens_salvos,
        'descritores': Descritor.objects.filter(disciplina=avaliacao.disciplina), 
        'tem_itens': itens_salvos.exists()
    }
    return render(request, 'core/definir_gabarito.html', context)

@login_required
def lancar_nota(request):
    avaliacao_id = request.GET.get('avaliacao_id')
    avaliacao_obj = None
    itens = []
    alunos_turma = []

    if avaliacao_id:
        avaliacao_obj = get_object_or_404(Avaliacao, id=avaliacao_id)
        itens = ItemGabarito.objects.filter(avaliacao=avaliacao_obj).order_by('numero')
        # CORREÇÃO: Filtra alunos ativos da turma
        alunos_turma = Aluno.objects.filter(turma=avaliacao_obj.turma, ativo=True).order_by('nome_completo')

    if request.method == 'POST' and avaliacao_obj:
        aluno_id = request.POST.get('aluno')
        if not aluno_id:
            messages.error(request, "Selecione um aluno.")
            return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

        aluno_obj = get_object_or_404(Aluno, id=aluno_id)
        resultado, _ = Resultado.objects.update_or_create(
            avaliacao=avaliacao_obj, aluno=aluno_obj,
            defaults={'total_questoes': itens.count(), 'acertos': 0}
        )

        RespostaDetalhada.objects.filter(resultado=resultado).delete()
        acertos_contagem = 0
        
        for item in itens:
            resposta_aluno = request.POST.get(f'resposta_{item.id}')
            acertou = False
            if resposta_aluno and resposta_aluno.strip().upper() == item.resposta_correta.upper():
                acertou = True
                acertos_contagem += 1

            RespostaDetalhada.objects.create(
                resultado=resultado, item_gabarito=item,
                questao=item.questao_banco, acertou=acertou
            )

        resultado.acertos = acertos_contagem
        resultado.save()
        messages.success(request, f'Nota salva: {acertos_contagem}')
        return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

    return render(request, 'core/lancar_nota.html', {
        'avaliacao_selecionada': avaliacao_obj,
        'itens': itens, 'alunos': alunos_turma,
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-data_aplicacao')
    })

# ==============================================================================
# 📋 GERENCIAMENTO GERAL
# ==============================================================================

# No seu core/views.py (Substitua a função gerenciar_alunos)

@login_required
def gerenciar_alunos(request):
    # --- LÓGICA DE AÇÕES (POST) ---
    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        # 1. CRIAR
        if acao == 'criar':
            nome = request.POST.get('nome')
            turma_id = request.POST.get('turma')
            if nome and turma_id:
                Aluno.objects.create(nome_completo=nome, turma_id=turma_id, ativo=True)
                messages.success(request, 'Aluno cadastrado com sucesso!')
            else:
                messages.error(request, 'Preencha todos os campos.')

        # 2. EDITAR
        elif acao == 'editar':
            aluno_id = request.POST.get('aluno_id')
            aluno = get_object_or_404(Aluno, id=aluno_id)
            aluno.nome_completo = request.POST.get('nome')
            aluno.turma_id = request.POST.get('turma')
            aluno.ativo = request.POST.get('ativo') == 'on' 
            aluno.save()
            messages.success(request, 'Dados atualizados!')

        # 3. EXCLUIR
        elif acao == 'excluir':
            aluno_id = request.POST.get('aluno_id')
            aluno = get_object_or_404(Aluno, id=aluno_id)
            aluno.delete()
            messages.warning(request, 'Aluno removido.')

        return redirect('gerenciar_alunos')

    # --- LÓGICA DE VISUALIZAÇÃO (GET) ---
    busca = request.GET.get('busca')
    filtro_turma = request.GET.get('turma')
    
    # AQUI MUDOU: Adicionamos o cálculo da média (annotate)
    alunos_list = Aluno.objects.select_related('turma') \
                               .annotate(media_geral=Avg('resultado__percentual')) \
                               .order_by('nome_completo')
    
    if busca:
        alunos_list = alunos_list.filter(nome_completo__icontains=busca)
    
    if filtro_turma:
        alunos_list = alunos_list.filter(turma_id=filtro_turma)

    paginator = Paginator(alunos_list, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    turmas = Turma.objects.all().order_by('nome')

    return render(request, 'core/gerenciar_alunos.html', {
        'page_obj': page_obj,
        'turmas': turmas,
        'busca_atual': busca,
        'turma_atual': int(filtro_turma) if filtro_turma else None
    })

@login_required
def gerenciar_avaliacoes(request):
    if request.method == 'POST' and 'delete_id' in request.POST:
        av = get_object_or_404(Avaliacao, id=request.POST.get('delete_id'))
        av.delete()
        messages.success(request, 'Avaliação removida!')
        return redirect('gerenciar_avaliacoes')

    turma_id = request.GET.get('turma')
    disciplina_id = request.GET.get('disciplina')
    avaliacoes = Avaliacao.objects.select_related('turma', 'disciplina').all().order_by('-data_aplicacao')
    
    if turma_id: avaliacoes = avaliacoes.filter(turma_id=turma_id)
    if disciplina_id: avaliacoes = avaliacoes.filter(disciplina_id=disciplina_id)

    return render(request, 'core/avaliacoes.html', {
        'avaliacoes': avaliacoes, 'turmas': Turma.objects.all(),
        'disciplinas': Disciplina.objects.all(), 'total_avaliacoes': avaliacoes.count()
    })

@login_required
def gerenciar_turmas(request):
    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'criar':
            Turma.objects.create(nome=request.POST.get('nome_turma'), ano_letivo=2026)
            messages.success(request, 'Turma criada!')
        elif acao == 'editar':
            t = Turma.objects.get(id=request.POST.get('id_turma'))
            t.nome = request.POST.get('novo_nome')
            t.save()
            messages.success(request, 'Turma editada!')
        elif acao == 'excluir':
            Turma.objects.get(id=request.POST.get('id_turma')).delete()
            messages.success(request, 'Turma excluída!')
        return redirect('gerenciar_turmas')

    turmas = Turma.objects.annotate(qtd_alunos=Count('aluno')).order_by('nome')
    return render(request, 'core/turmas.html', {'turmas': turmas})


@login_required
def listar_questoes(request):
    # --- LÓGICA DE POST (CRUD - Mantida igual) ---
    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        if acao == 'excluir':
            questao_id = request.POST.get('questao_id')
            if questao_id:
                q = get_object_or_404(Questao, id=questao_id)
                q.delete()
                messages.success(request, 'Questão excluída com sucesso.')
            
        elif acao == 'salvar':
            questao_id = request.POST.get('questao_id')
            
            # Dados do formulário
            dados = {
                'enunciado': request.POST.get('enunciado'),
                'disciplina_id': request.POST.get('disciplina'),
                'dificuldade': request.POST.get('dificuldade'),
                'serie': request.POST.get('serie'),
                'gabarito': request.POST.get('gabarito'),
                'alternativa_a': request.POST.get('alternativa_a'),
                'alternativa_b': request.POST.get('alternativa_b'),
                'alternativa_c': request.POST.get('alternativa_c'),
                'alternativa_d': request.POST.get('alternativa_d'),
                'alternativa_e': request.POST.get('alternativa_e'),
            }

            if 'imagem_arquivo' in request.FILES:
                dados['imagem'] = request.FILES['imagem_arquivo']
            
            desc_cod = request.POST.get('descritor_cod')
            if desc_cod:
                desc_obj, _ = Descritor.objects.get_or_create(
                    codigo=desc_cod, 
                    defaults={'disciplina_id': dados['disciplina_id'], 'descricao': 'Criado manualmente'}
                )
                dados['descritor'] = desc_obj
            
            try:
                if questao_id: # Edição
                    q = Questao.objects.get(id=questao_id)
                    for key, value in dados.items():
                        setattr(q, key, value)
                    q.save()
                    messages.success(request, 'Questão atualizada!')
                else: # Criação
                    Questao.objects.create(**dados)
                    messages.success(request, 'Nova questão criada!')
            except Exception as e:
                messages.error(request, f'Erro ao salvar: {str(e)}')
                
        return redirect('listar_questoes')

    # --- LÓGICA DE VISUALIZAÇÃO (GET - Atualizada com Filtros) ---
    questoes = Questao.objects.select_related('disciplina', 'descritor').order_by('-id')
    
    # Captura os parâmetros da URL
    filtro_disc = request.GET.get('disciplina')
    filtro_busca = request.GET.get('busca')
    filtro_dificuldade = request.GET.get('dificuldade') # Novo
    filtro_serie = request.GET.get('serie')             # Novo
    
    # 1. Filtro de Disciplina
    if filtro_disc and filtro_disc not in ['None', '']: 
        try:
            questoes = questoes.filter(disciplina_id=int(filtro_disc))
        except ValueError:
            pass 
            
    # 2. Filtro de Busca
    if filtro_busca and filtro_busca not in ['None', '']:
        questoes = questoes.filter(enunciado__icontains=filtro_busca)

    # 3. Filtro de Dificuldade (Novo)
    if filtro_dificuldade and filtro_dificuldade in ['F', 'M', 'D']:
        questoes = questoes.filter(dificuldade=filtro_dificuldade)

    # 4. Filtro de Série (Novo)
    if filtro_serie and filtro_serie in ['1', '2', '3']:
        questoes = questoes.filter(serie=filtro_serie)
    
    paginator = Paginator(questoes, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    context = {
        'page_obj': page_obj,
        'disciplinas': Disciplina.objects.all(),
        # Passa os filtros de volta para o template manter selecionado
        'filtro_disciplina': int(filtro_disc) if (filtro_disc and filtro_disc.isdigit()) else None,
        'busca_atual': filtro_busca if filtro_busca else '',
        'filtro_dificuldade': filtro_dificuldade,
        'filtro_serie': filtro_serie
    }
    return render(request, 'core/listar_questoes.html', context)

@login_required
def editar_avaliacao(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    if request.method == 'POST':
        avaliacao.titulo = request.POST.get('titulo')
        avaliacao.turma_id = request.POST.get('turma')
        avaliacao.disciplina_id = request.POST.get('disciplina')
        avaliacao.data_aplicacao = request.POST.get('data_aplicacao')
        avaliacao.save()
        messages.success(request, 'Avaliação atualizada!')
        return redirect('gerenciar_avaliacoes')
    
    context = {
        'avaliacao': avaliacao, 'turmas': Turma.objects.all(),
        'disciplinas': Disciplina.objects.all(),
        'data_formatada': avaliacao.data_aplicacao.strftime('%Y-%m-%d') if avaliacao.data_aplicacao else ''
    }
    return render(request, 'core/editar_avaliacao.html', context)

# ==============================================================================
# 📊 RELATÓRIO DE PROFICIÊNCIA (LAYOUT "FEINHO" -> "BONITÃO")
# ==============================================================================

@login_required
def gerar_relatorio_proficiencia(request):
    # 1. Recupera Filtros (Mantido igual)
    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')

    # Config da Escola
    config = ConfiguracaoSistema.objects.first()
    nome_escola = config.nome_escola if config else "SAMI EDUCACIONAL"
    cor_pri = colors.HexColor(config.cor_primaria) if config else colors.HexColor("#1e293b")
    cor_sec = colors.HexColor(config.cor_secundaria) if config else colors.HexColor("#3b82f6")

    # 2. Filtra Dados (Mantido igual)
    resultados = Resultado.objects.all()
    filtros_texto = []

    if disciplina_id:
        resultados = resultados.filter(avaliacao__disciplina_id=disciplina_id)
        filtros_texto.append(f"Disciplina: {Disciplina.objects.get(id=disciplina_id).nome}")
    if turma_id:
        resultados = resultados.filter(avaliacao__turma_id=turma_id)
        filtros_texto.append(f"Turma: {Turma.objects.get(id=turma_id).nome}")
    if avaliacao_id:
        resultados = resultados.filter(avaliacao_id=avaliacao_id)
        filtros_texto.append(f"Prova: {Avaliacao.objects.get(id=avaliacao_id).titulo}")
    
    if not filtros_texto: filtros_texto.append("Visão Geral")

    # 3. Processa Dados (Agregação)
    respostas_qs = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related(
        'item_gabarito__descritor', 'questao__descritor'
    )

    stats = {}
    for resp in respostas_qs:
        desc = None
        if resp.item_gabarito and resp.item_gabarito.descritor: desc = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor: desc = resp.questao.descritor
        
        if desc:
            cod = desc.codigo
            if cod not in stats: stats[cod] = {'desc': desc.descricao, 'total': 0, 'acertos': 0}
            stats[cod]['total'] += 1
            if resp.acertou: stats[cod]['acertos'] += 1

    dados_ordenados = sorted(stats.items())

    # --- 4. GERA O PDF PREMIUM ---
    buffer = io.BytesIO()
    # Margens menores para caber mais
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    styles = getSampleStyleSheet()

    # --- CABEÇALHO COM LOGO E ONDA ---
    # Como o SimpleDocTemplate é 'high level', não desenhamos canvas direto aqui.
    # Vamos usar uma Tabela para o cabeçalho.
    
    # Título Principal
    header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=16, textColor=cor_pri, spaceAfter=2, fontName='Helvetica-Bold')
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, textColor=colors.grey, spaceAfter=12)
    
    elements.append(Paragraph(f"{nome_escola.upper()}", header_style))
    elements.append(Paragraph("RELATÓRIO PEDAGÓGICO DE PROFICIÊNCIA", sub_style))
    elements.append(Spacer(1, 10))
    
    # Caixa de Contexto (Fundo colorido)
    contexto_texto = " | ".join(filtros_texto)
    data_geracao = datetime.now().strftime('%d/%m/%Y às %H:%M')
    
    t_ctx = Table([[f"CONTEXTO: {contexto_texto}", f"EMISSÃO: {data_geracao}"]], colWidths=[380, 150])
    t_ctx.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f1f5f9")),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('PADDING', (0,0), (-1,-1), 8),
        ('ROUNDED', (0,0), (-1,-1), 6), # Cantos arredondados (se suportado)
    ]))
    elements.append(t_ctx)
    elements.append(Spacer(1, 20))

    # --- TABELA DE DADOS ---
    data_table = [['CÓDIGO', 'DESCRIÇÃO DA HABILIDADE', 'QTD', '% ACERTO', 'NÍVEL']]

    for cod, d in dados_ordenados:
        perc = (d['acertos'] / d['total']) * 100 if d['total'] > 0 else 0
        
        # Cores de Nível (Bolinhas)
        cor_nivel = colors.red
        nivel_txt = "CRÍTICO"
        if perc >= 80: 
            cor_nivel = colors.green; nivel_txt = "ADEQUADO"
        elif perc >= 60: 
            cor_nivel = colors.orange; nivel_txt = "INTERMED."
        
        # Descrição com quebra de linha
        desc_para = Paragraph(d['desc'], ParagraphStyle('d', fontSize=8, leading=9))
        
        # Estilo da Célula de Nível (Texto colorido)
        nivel_para = Paragraph(f"<font color='{cor_nivel.hexval()}'><b>{nivel_txt}</b></font>", ParagraphStyle('n', alignment=1))

        row = [
            Paragraph(f"<b>{cod}</b>", styles['Normal']),
            desc_para,
            str(d['total']),
            f"{perc:.1f}%",
            nivel_para
        ]
        data_table.append(row)

    # Estilo Moderno da Tabela
    t = Table(data_table, colWidths=[50, 330, 40, 60, 70])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), cor_pri), # Cabeçalho com cor da escola
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'), # Descrição à esquerda
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")), # Linhas finas cinzas
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]), # Zebra suave
    ]))
    
    elements.append(t)
    
    # Rodapé
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"<i>Sistema de Avaliação {nome_escola}</i>", ParagraphStyle('footer', fontSize=8, textColor=colors.grey, alignment=1)))

    doc.build(elements)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f"Proficiencia_{datetime.now().strftime('%Y%m%d')}.pdf")

@login_required
def api_filtrar_alunos(request):
    turma_id = request.GET.get('turma_id')
    alunos = Aluno.objects.filter(turma_id=turma_id).order_by('nome_completo') if turma_id else []
    return JsonResponse([{'id': a.id, 'nome': a.nome_completo} for a in alunos], safe=False)


# PERFIL DO ALUNO E DESEMPENHO.

# No final de core/views.py

@login_required
def perfil_aluno(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    
    # 1. Histórico de Resultados
    resultados = Resultado.objects.filter(aluno=aluno).select_related('avaliacao', 'avaliacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    # 2. Dados para o Gráfico de Evolução
    labels_evo = [res.avaliacao.titulo[:15] + '...' for res in resultados] # Títulos curtos
    dados_evo = [float(res.percentual) for res in resultados]
    
    # 3. Média Geral
    media_geral = sum(dados_evo) / len(dados_evo) if dados_evo else 0
    
    # 4. Análise de Habilidades (Pontos Fortes e Fracos)
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related('item_gabarito__descritor', 'questao__descritor')
    stats_descritores = {}
    
    for resp in respostas:
        # Tenta pegar o descritor do item ou da questão
        desc = None
        if resp.item_gabarito and resp.item_gabarito.descritor:
            desc = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor:
            desc = resp.questao.descritor
            
        if desc:
            cod = desc.codigo
            if cod not in stats_descritores:
                stats_descritores[cod] = {'obj': desc, 'acertos': 0, 'total': 0}
            
            stats_descritores[cod]['total'] += 1
            if resp.acertou: stats_descritores[cod]['acertos'] += 1
            
    # Calcula porcentagens e ordena
    lista_habilidades = []
    for cod, dados in stats_descritores.items():
        perc = (dados['acertos'] / dados['total']) * 100
        lista_habilidades.append({
            'codigo': cod,
            'descricao': dados['obj'].descricao,
            'tema': dados['obj'].tema,
            'perc': round(perc, 1),
            'total_questoes': dados['total']
        })
    
    # Ordena: Melhores primeiro
    habilidades_fortes = sorted(lista_habilidades, key=lambda x: x['perc'], reverse=True)[:5]
    # Ordena: Piores primeiro (mas filtra os que tem 0% ou muito baixo para não pegar erros aleatórios de 1 questão só)
    habilidades_fracas = sorted([h for h in lista_habilidades if h['perc'] < 60], key=lambda x: x['perc'])[:5]

    context = {
        'aluno': aluno,
        'media_geral': round(media_geral, 1),
        'total_provas': resultados.count(),
        'labels_evo': json.dumps(labels_evo),
        'dados_evo': json.dumps(dados_evo),
        'habilidades_fortes': habilidades_fortes,
        'habilidades_fracas': habilidades_fracas,
        'historico': resultados.order_by('-avaliacao__data_aplicacao')
    }
    
    return render(request, 'core/perfil_aluno.html', context)

# Em core/views.py
@login_required
def mapa_calor(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # 1. Pegamos todas as questões (colunas)
    itens = ItemGabarito.objects.filter(avaliacao=avaliacao).select_related('descritor').order_by('numero')
    
    # 2. Pegamos todos os alunos que fizeram a prova (linhas)
    resultados = Resultado.objects.filter(avaliacao=avaliacao).select_related('aluno').order_by('aluno__nome_completo')
    
    matriz_dados = []
    
    for res in resultados:
        # Pega as respostas desse aluno específico
        respostas = RespostaDetalhada.objects.filter(resultado=res)
        mapa_respostas = {r.item_gabarito_id: r.acertou for r in respostas}
        
        linha_questoes = []
        acertos_count = 0
        
        for item in itens:
            status = mapa_respostas.get(item.id) # True, False ou None
            linha_questoes.append({
                'numero': item.numero,
                'acertou': status,
                'descritor': item.descritor.codigo if item.descritor else '-'
            })
            if status: acertos_count += 1
            
        # CORREÇÃO AQUI: Calculamos a nota baseada no percentual
        nota_calculada = round(res.percentual / 10, 1) if res.percentual else 0.0

        matriz_dados.append({
            'aluno': res.aluno,
            'questoes': linha_questoes,
            'nota': nota_calculada, # Agora usamos a variável calculada
            'total_acertos': acertos_count
        })

    # Estatísticas por Questão (Rodapé)
    stats_questoes = []
    total_alunos = resultados.count() or 1
    for item in itens:
        qtd_acertos = RespostaDetalhada.objects.filter(item_gabarito=item, acertou=True).count()
        perc = (qtd_acertos / total_alunos) * 100
        stats_questoes.append({'numero': item.numero, 'perc': round(perc)})

    context = {
        'avaliacao': avaliacao,
        'itens': itens,
        'matriz': matriz_dados,
        'stats_questoes': stats_questoes
    }
    
    return render(request, 'core/mapa_calor.html', context)


# Adicione essas importações se faltar alguma
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
import io

def gerar_boletim_pdf(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    # Pega resultados ordenados
    resultados = Resultado.objects.filter(aluno=aluno).select_related('avaliacao', 'avaliacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    # --- 1. PROCESSAMENTO DE DADOS ---
    dados_grafico = [] 
    dados_tabela = []
    soma_notas = 0
    ultima_nota = 0
    nota_anterior = 0
    
    if resultados.exists():
        for i, res in enumerate(resultados):
            nota_aluno = round(res.percentual / 10, 1)
            
            # Calcula média da turma para comparar
            media_turma_val = Resultado.objects.filter(avaliacao=res.avaliacao).aggregate(Avg('percentual'))['percentual__avg'] or 0
            nota_turma = round(media_turma_val / 10, 1)
            
            dados_grafico.append({
                'aluno': nota_aluno,
                'turma': nota_turma,
                'label': res.avaliacao.data_aplicacao.strftime("%d/%m")
            })
            soma_notas += nota_aluno
            
            status = "ACIMA" if nota_aluno >= nota_turma else "ABAIXO"
            if nota_aluno < 6: status = "CRÍTICO"
            
            dados_tabela.append([
                res.avaliacao.data_aplicacao.strftime("%d/%m/%Y"),
                res.avaliacao.titulo[:22], # Limita caracteres
                res.avaliacao.disciplina.nome[:15] if res.avaliacao.disciplina else "-",
                str(nota_aluno),
                str(nota_turma),
                status
            ])

            # Guarda histórico para tendência
            if i == len(resultados) - 1: ultima_nota = nota_aluno
            if i == len(resultados) - 2: nota_anterior = nota_aluno
            
        media_geral = round(soma_notas / len(resultados), 1)
    else:
        media_geral = 0.0

    # --- 1.1 PROCESSAMENTO DE HABILIDADES (NOVIDADE) ---
    # Busca todas as respostas desse aluno
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related('item_gabarito__descritor', 'questao__descritor')
    
    stats_habilidades = {}
    for resp in respostas:
        desc = None
        # Tenta pegar descritor do Item ou da Questão
        if resp.item_gabarito and resp.item_gabarito.descritor: desc = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor: desc = resp.questao.descritor
        
        if desc:
            if desc.codigo not in stats_habilidades:
                stats_habilidades[desc.codigo] = {'texto': desc.descricao, 'total': 0, 'acertos': 0}
            stats_habilidades[desc.codigo]['total'] += 1
            if resp.acertou: stats_habilidades[desc.codigo]['acertos'] += 1
            
    # Classifica Habilidades
    pontos_fortes = []
    pontos_atencao = []
    
    for cod, dados in stats_habilidades.items():
        perc = (dados['acertos'] / dados['total']) * 100
        texto_fmt = f"{cod} - {dados['texto'][:35]}..."
        if perc >= 70: pontos_fortes.append(texto_fmt)
        elif perc <= 50: pontos_atencao.append(texto_fmt)
    
    # Pega só os top 3 de cada para não encher a folha
    pontos_fortes = pontos_fortes[:3]
    pontos_atencao = pontos_atencao[:3]


    # --- 2. SETUP VISUAL (CANVAS) ---
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Cores
    COR_DEEP = colors.HexColor("#1e293b") 
    COR_ACCENT = colors.HexColor("#3b82f6") 
    COR_LIGHT = colors.HexColor("#f1f5f9") 
    COR_TEXT = colors.HexColor("#334155") 
    COR_SUCCESS = colors.HexColor("#10b981")
    COR_DANGER = colors.HexColor("#ef4444")

    # --- 3. CABEÇALHO ---
    # Onda Fundo
    p = c.beginPath()
    p.moveTo(0, height)
    p.lineTo(width, height)
    p.lineTo(width, height - 120)
    p.curveTo(width, height - 120, width/2, height - 200, 0, height - 120)
    p.close()
    c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.2))
    c.drawPath(p, fill=1, stroke=0)

    # Onda Principal
    p2 = c.beginPath()
    p2.moveTo(0, height)
    p2.lineTo(width, height)
    p2.lineTo(width, height - 110)
    p2.curveTo(width, height - 110, width/2, height - 160, 0, height - 110)
    p2.close()
    c.setFillColor(COR_DEEP)
    c.drawPath(p2, fill=1, stroke=0)

    # Textos Header
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(40, height - 60, "RELATÓRIO DE DESEMPENHO")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 80, "SAMI EDUCACIONAL • Acompanhamento Integrado")
    
    # Badge Ano
    c.roundRect(width - 100, height - 70, 60, 25, 6, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width - 70, height - 64, str(datetime.now().year))

    # --- 4. INFO ALUNO ---
    y_info = height - 190
    
    # Foto
    c.setStrokeColor(COR_ACCENT)
    c.setFillColor(colors.white)
    c.circle(70, y_info, 35, fill=1, stroke=1)
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(70, y_info - 8, aluno.nome_completo[0])
    
    # Texto Info
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(120, y_info + 10, aluno.nome_completo[:35])
    c.setFillColor(COR_TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(120, y_info - 10, f"Matrícula: #{aluno.id}  •  Turma: {aluno.turma.nome}")
    
    # Card Média Geral
    c.setFillColor(COR_LIGHT)
    c.roundRect(width - 160, y_info - 25, 120, 60, 10, fill=1, stroke=0)
    
    label_media = "EXCELENTE" if media_geral >= 8 else "REGULAR" if media_geral >= 6 else "ATENÇÃO"
    cor_media = COR_SUCCESS if media_geral >= 6 else COR_DANGER
    
    c.setFillColor(colors.grey)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(width - 100, y_info + 20, "MÉDIA GERAL")
    c.setFillColor(cor_media)
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(width - 100, y_info - 5, str(media_geral))
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(width - 100, y_info - 18, label_media)

    # --- 5. GRÁFICO ---
    y_graph_top = y_info - 80
    graph_h = 100 # Reduzi um pouco para caber as habilidades
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y_graph_top, "Evolução do Bimestre")
    
    y_base = y_graph_top - graph_h - 20
    center_x = width / 2
    
    # Linha base
    c.setStrokeColor(colors.lightgrey)
    c.setLineWidth(1)
    c.line(40, y_base, width - 40, y_base)

    if len(dados_grafico) > 0:
        # Lógica de Gráfico (Simplificada para Area Chart sempre que possível)
        graph_width = 450
        x_start = 65
        
        # Se tiver só 1 ponto, desenha barra
        if len(dados_grafico) == 1:
            dado = dados_grafico[0]
            c.setFillColor(COR_ACCENT)
            h_bar = (dado['aluno'] / 10) * graph_h
            c.roundRect(center_x - 20, y_base, 40, h_bar, 4, fill=1, stroke=0)
            c.setFillColor(COR_DEEP)
            c.drawCentredString(center_x, y_base + h_bar + 5, str(dado['aluno']))
            c.drawCentredString(center_x, y_base - 15, dado['label'])
        else:
            # Area Chart
            step_x = graph_width / (len(dados_grafico) - 1)
            coords_x = [x_start + (i * step_x) for i in range(len(dados_grafico))]
            
            # Area Colorida
            p = c.beginPath()
            p.moveTo(coords_x[0], y_base)
            for i in range(len(dados_grafico)):
                y_pt = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
                p.lineTo(coords_x[i], y_pt)
            p.lineTo(coords_x[-1], y_base)
            p.close()
            c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.15))
            c.drawPath(p, fill=1, stroke=0)
            
            # Linha
            c.setStrokeColor(COR_ACCENT); c.setLineWidth(2)
            for i in range(len(dados_grafico) - 1):
                y1 = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
                y2 = y_base + (dados_grafico[i+1]['aluno'] / 10 * graph_h)
                c.line(coords_x[i], y1, coords_x[i+1], y2)
                
            # Pontos e Labels
            for i in range(len(dados_grafico)):
                cy = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
                c.setFillColor(colors.white); c.setStrokeColor(COR_ACCENT)
                c.circle(coords_x[i], cy, 3, fill=1, stroke=1)
                c.setFillColor(COR_DEEP)
                c.setFont("Helvetica", 8)
                c.drawCentredString(coords_x[i], y_base - 12, dados_grafico[i]['label'])
                c.setFont("Helvetica-Bold", 8)
                c.drawCentredString(coords_x[i], cy + 8, str(dados_grafico[i]['aluno']))

    # --- 6. TABELA DE NOTAS ---
    y_table_title = y_base - 50
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y_table_title, "Histórico de Provas")
    
    header = ['DATA', 'AVALIAÇÃO', 'DISCIPLINA', 'NOTA', 'TURMA', 'STATUS']
    table_data_full = [header] + dados_tabela
    if not dados_tabela: table_data_full.append(['-']*6)

    t = Table(table_data_full, colWidths=[50, 180, 110, 50, 50, 80])
    
    estilo = [
        ('BACKGROUND', (0,0), (-1,0), COR_DEEP),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (3,0), (5,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('TEXTCOLOR', (0,1), (-1,-1), COR_TEXT),
    ]
    
    # Pinta as notas
    for idx, row in enumerate(dados_tabela):
        linha = idx + 1
        nota = float(row[3])
        cor = COR_SUCCESS if nota >= 6 else COR_DANGER
        estilo.append(('TEXTCOLOR', (3, linha), (3, linha), cor))
        estilo.append(('FONTNAME', (3, linha), (3, linha), 'Helvetica-Bold'))
        
        status_cor = COR_SUCCESS if row[5] == "ACIMA" else COR_DANGER if row[5] == "CRÍTICO" else colors.orange
        estilo.append(('TEXTCOLOR', (5, linha), (5, linha), status_cor))

    t.setStyle(TableStyle(estilo))
    w_t, h_t = t.wrapOn(c, width, height)
    t.drawOn(c, 40, y_table_title - h_t - 10)
    
    # Atualiza posição Y atual
    y_current = y_table_title - h_t - 40

    # --- 7. QUADRO DE HABILIDADES (RAIO-X) ---
    if pontos_fortes or pontos_atencao:
        c.setFillColor(COR_DEEP)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y_current, "Raio-X de Habilidades (Pedagógico)")
        y_current -= 20

        # Vamos criar duas tabelas lado a lado
        data_hab = [['DOMINADAS (+70%)', 'EM DESENVOLVIMENTO (-50%)']]
        
        # Preenche com dados (até o máximo de linhas de um dos dois)
        max_len = max(len(pontos_fortes), len(pontos_atencao))
        if max_len == 0: max_len = 1 # Garante pelo menos uma linha
        
        for i in range(max_len):
            forte = pontos_fortes[i] if i < len(pontos_fortes) else ""
            fraco = pontos_atencao[i] if i < len(pontos_atencao) else ""
            data_hab.append([forte, fraco])

        t_hab = Table(data_hab, colWidths=[255, 255])
        t_hab.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), colors.HexColor("#dcfce7")), # Verde Claro Header
            ('BACKGROUND', (1,0), (1,0), colors.HexColor("#fee2e2")), # Vermelho Claro Header
            ('TEXTCOLOR', (0,0), (0,0), colors.darkgreen),
            ('TEXTCOLOR', (1,0), (1,0), colors.darkred),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ]))
        
        w_hab, h_hab = t_hab.wrapOn(c, width, height)
        t_hab.drawOn(c, 40, y_current - h_hab)
    
    # --- 8. RODAPÉ / DIAGNÓSTICO (CORRIGIDO PARA QUEBRA DE LINHA) ---
    y_footer = 50
    
    # Lógica de Diagnóstico
    tendencia = ""
    if len(resultados) >= 2:
        if ultima_nota > nota_anterior: tendencia = " Observa-se uma tendência de evolução positiva."
        elif ultima_nota < nota_anterior: tendencia = " Observa-se uma leve queda recente que requer atenção."

    msg_texto = ""
    if media_geral >= 8: msg_texto = f"Desempenho excelente! O aluno demonstra domínio consistente dos conteúdos.{tendencia}"
    elif media_geral >= 6: msg_texto = f"Desempenho satisfatório. Atende às expectativas, mas pode avançar mais.{tendencia}"
    else: msg_texto = f"Situação de alerta. O aluno encontra-se abaixo da média, sendo fortemente recomendado reforço escolar.{tendencia}"

    # Desenha o fundo da caixa
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.roundRect(40, y_footer, width - 80, 50, 6, fill=1, stroke=0)
    
    # Título do Parecer
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y_footer + 32, "PARECER DO SISTEMA:")
    
    # Texto do Parecer com Quebra Automática (Paragraph)
    styles = getSampleStyleSheet()
    estilo_parecer = ParagraphStyle(
        'ParecerStyle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=COR_TEXT,
        leading=11 # Espaçamento entre linhas
    )
    
    # Cria o parágrafo
    parecer_para = Paragraph(msg_texto, estilo_parecer)
    
    # Define largura máxima (width - margem esq - espaço titulo - margem dir)
    largura_disponivel = width - 160 - 50 
    
    w_p, h_p = parecer_para.wrap(largura_disponivel, 40) # Tenta encaixar
    parecer_para.drawOn(c, 160, y_footer + 38 - h_p) # Posiciona (ajuste vertical baseada na altura)

    # Espaço Assinatura
    c.setStrokeColor(colors.grey)
    c.line(width - 200, y_footer + 15, width - 40, y_footer + 15)
    c.setFont("Helvetica", 6)
    c.drawCentredString(width - 120, y_footer + 8, "Assinatura do Responsável")

    c.showPage()
    c.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'Boletim_{aluno.nome_completo}.pdf')

# ==========================================
# 2. GERADOR DE CARTÕES (COM QR CODE)      #
# ==========================================
@login_required
def gerar_cartoes_pdf(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # ... (Seleção de alunos continua igual) ...
    if avaliacao.aluno:
        alunos = [avaliacao.aluno]
    else:
        alunos = Aluno.objects.filter(turma=avaliacao.turma).order_by('nome_completo')
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 1 * cm
    
    card_w = (width - (3 * margin)) / 2
    card_h = (height - (3 * margin)) / 2
    
    positions = [
        (margin, height - margin - card_h), 
        (margin + card_w + margin, height - margin - card_h),
        (margin, margin),
        (margin + card_w + margin, margin)
    ]
    
    # BUSCA TOTAL DE QUESTÕES
    total_questoes = ItemGabarito.objects.filter(avaliacao=avaliacao).count() or 10

    # LÓGICA DE COLUNAS (CORREÇÃO AQUI)
    # Define quantas questões cabem na coluna 1 antes de pular para a 2
    # Se tiver muitas questões (> 20), vamos tentar balancear melhor
    # Capacidade máxima visual da coluna ~17 questões
    limite_coluna_1 = 15 
    
    aluno_idx = 0
    total_alunos = len(alunos)
    
    while aluno_idx < total_alunos:
        for pos_x, pos_y in positions:
            if aluno_idx >= total_alunos: break
            
            aluno = alunos[aluno_idx]
            
            # ... (Desenho da borda e marcadores CONTINUA IGUAL) ...
            c.setStrokeColor(colors.black)
            c.setLineWidth(1)
            c.setDash([2, 4])
            c.rect(pos_x, pos_y, card_w, card_h, stroke=1, fill=0)
            c.setDash([])

            # Marcadores Fiduciais
            c.setFillColor(colors.black)
            marker_size = 15
            c.rect(pos_x + 10, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + 10, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)

            # QR Code (Mantido no canto inferior direito)
            qr_data = f"A{avaliacao.id}-U{aluno.id}"
            qr = qrcode.QRCode(box_size=2, border=0)
            qr.add_data(qr_data)
            qr.make(fit=True)
            img_qr = qr.make_image(fill_color="black", back_color="white")
            qr_img_reader = ImageReader(img_qr._img)
            
            # Posição do QR Code
            c.drawImage(qr_img_reader, pos_x + card_w - 70, pos_y + 20, width=50, height=50)
            
            # Cabeçalho
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(pos_x + 35, pos_y + card_h - 25, "CARTÃO RESPOSTA")
            c.setFont("Helvetica", 9)
            c.drawString(pos_x + 35, pos_y + card_h - 45, f"Aluno: {aluno.nome_completo[:25]}")
            c.drawString(pos_x + 35, pos_y + card_h - 58, f"Prova: {avaliacao.titulo[:25]}")
            c.setFont("Helvetica", 8)
            c.drawString(pos_x + 35, pos_y + card_h - 70, f"Turma: {aluno.turma.nome} | Cód.: {aluno.id}")
            
            # --- DESENHO DAS BOLINHAS (CORRIGIDO) ---
            y_start = pos_y + card_h - 95
            x_col1 = pos_x + 30
            x_col2 = pos_x + card_w/2 + 10 # Um pouco mais pra esquerda pra caber
            
            c.setFont("Helvetica", 9)
            
            for q_num in range(1, total_questoes + 1):
                # Se for menor que o limite, desenha na esquerda
                if q_num <= limite_coluna_1:
                    curr_x = x_col1
                    curr_y = y_start - ((q_num - 1) * 16)
                else:
                    # Coluna da direita
                    # CUIDADO: Se tiver muitas questões aqui, vai bater no QR Code
                    # O QR Code começa em Y + 20 e tem altura 50. Vai até Y + 70.
                    # Precisamos parar antes de Y + 80.
                    
                    idx_col2 = q_num - limite_coluna_1 - 1
                    curr_x = x_col2
                    curr_y = y_start - (idx_col2 * 16)
                    
                    # Checagem de segurança (Colisão com QR Code)
                    if curr_y < (pos_y + 80): 
                        # Se for bater no QR Code, joga um pouco pra esquerda?
                        # Ou avisa que não cabe.
                        # Por enquanto, vamos apenas recuar o X para não ficar EM CIMA do QR
                        # O QR Code está em X + card_w - 70.
                        # Nossa coluna 2 está em card_w/2 + 10.
                        # Se card_w é ~280, metade é 140. QR começa em 210.
                        # As bolinhas ocupam uns 100px. 140+100 = 240. Bate!
                        
                        # SOLUÇÃO: Mover a segunda coluna mais para a esquerda se estivermos na parte de baixo
                        curr_x = x_col2 - 20 

                c.drawString(curr_x, curr_y, str(q_num).zfill(2))
                
                opcoes = ['A', 'B', 'C', 'D', 'E']
                for i, opt in enumerate(opcoes):
                    bubble_x = curr_x + 25 + (i * 14)
                    bubble_y = curr_y + 3
                    c.circle(bubble_x, bubble_y, 5.5, stroke=1, fill=0)
                    c.setFont("Helvetica", 6)
                    c.drawCentredString(bubble_x, bubble_y - 2, opt)
                    c.setFont("Helvetica", 9)

            aluno_idx += 1
            
        c.showPage() 

    c.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'Cartoes_{avaliacao.titulo}.pdf')


@login_required
def gerenciar_ndi(request):
    turma_id = request.GET.get('turma')
    bimestre = int(request.GET.get('bimestre', 1))
    
    turmas = Turma.objects.all().order_by('nome')
    alunos_data = []
    turma_selecionada = None

    # --- FUNÇÃO AUXILIAR PARA LIMPAR A NOTA ---
    def tratar_nota(valor_str):
        if not valor_str or valor_str.strip() == '':
            return 0.0
        try:
            # Troca vírgula por ponto e converte
            valor = float(valor_str.replace(',', '.'))
            # Trava entre 0 e 10
            if valor < 0: return 0.0
            if valor > 10: return 10.0
            return valor
        except ValueError:
            return 0.0
    # ------------------------------------------

    if turma_id:
        turma_selecionada = get_object_or_404(Turma, id=turma_id)
        alunos = Aluno.objects.filter(turma_id=turma_id, ativo=True).order_by('nome_completo')
        
        # Salvar Dados (POST)
        if request.method == 'POST':
            salvos = 0
            for aluno in alunos:
                # Usa a função auxiliar para proteger contra erros
                freq = tratar_nota(request.POST.get(f'freq_{aluno.id}'))
                atv = tratar_nota(request.POST.get(f'atv_{aluno.id}'))
                comp = tratar_nota(request.POST.get(f'comp_{aluno.id}'))
                pp = tratar_nota(request.POST.get(f'pp_{aluno.id}'))
                pb = tratar_nota(request.POST.get(f'pb_{aluno.id}'))
                
                # Salva ou Atualiza
                NDI.objects.update_or_create(
                    aluno=aluno, bimestre=bimestre,
                    defaults={
                        'turma': turma_selecionada,
                        'nota_frequencia': freq, 
                        'nota_atividade': atv,
                        'nota_comportamento': comp,
                        'nota_prova_parcial': pp, 
                        'nota_prova_bimestral': pb
                    }
                )
                salvos += 1
            messages.success(request, f"NDI salva para {salvos} alunos no {bimestre}º Bimestre!")
            return redirect(f"{request.path}?turma={turma_id}&bimestre={bimestre}")

        # Preparar dados para exibição
        for aluno in alunos:
            # Corrigido: Adicionado NDI na importação lá no topo se ainda não tiver
            ndi = NDI.objects.filter(aluno=aluno, bimestre=bimestre).first()
            alunos_data.append({
                'obj': aluno,
                'ndi': ndi
            })

    return render(request, 'core/gerenciar_ndi.html', {
        'turmas': turmas,
        'turma_selecionada': turma_selecionada,
        'alunos_data': alunos_data,
        'bimestre_atual': bimestre,
        'bimestres_opts': [1, 2, 3, 4]
    })

@login_required
def plano_anual(request):
    turma_id = request.GET.get('turma')
    disciplina_selecionada = request.GET.get('disciplina', 'Matemática') # Padrão
    
    turmas = Turma.objects.all()
    
    # Lista de Disciplinas (Idealmente viria do banco, mas vamos fixar para teste)
    disciplinas = ['Matemática', 'Português', 'História', 'Geografia', 'Ciências']

    plano = None
    # Estrutura: { Bimestre: { 'TODO': [], 'DOING': [], 'DONE': [] } }
    dados_kanban = {} 

    # Inicializa a estrutura vazia para os 4 bimestres
    for b in range(1, 5):
        dados_kanban[b] = {'TODO': [], 'DOING': [], 'DONE': []}

    if turma_id:
        turma = get_object_or_404(Turma, id=turma_id)
        
        # Cria ou recupera o plano daquela matéria específica
        plano, created = PlanoEnsino.objects.get_or_create(
            turma=turma, 
            disciplina_nome=disciplina_selecionada, 
            defaults={'ano_letivo': 2026}
        )

        # POST: Adicionar Novo Tópico
        if request.method == 'POST':
            acao = request.POST.get('acao') # identificar o que fazer

            if 'arquivo_plano' in request.FILES:
                plano.arquivo = request.FILES['arquivo_plano']
                plano.save()
                messages.success(request, "Arquivo anexado!")
            
            elif acao == 'criar':
                conteudo = request.POST.get('conteudo')
                bimestre = int(request.POST.get('bimestre'))
                if conteudo:
                    TopicoPlano.objects.create(plano=plano, bimestre=bimestre, conteudo=conteudo, status='TODO')
                    messages.success(request, "Tópico criado!")

            elif acao == 'editar':
                topico_id = request.POST.get('topico_id')
                topico = get_object_or_404(TopicoPlano, id=topico_id)
                topico.conteudo = request.POST.get('conteudo')
                topico.save()
                messages.success(request, "Tópico atualizado!")

            elif acao == 'excluir':
                topico_id = request.POST.get('topico_id')
                TopicoPlano.objects.filter(id=topico_id).delete()
                messages.warning(request, "Tópico removido.")
            
            return redirect(f"{request.path}?turma={turma_id}&disciplina={disciplina_selecionada}")

        # Organizar os tópicos nas colunas certas
        topicos = plano.topicos.all().order_by('id')
        for t in topicos:
            dados_kanban[t.bimestre][t.status].append(t)

    return render(request, 'core/plano_anual.html', {
        'turmas': turmas,
        'disciplinas': disciplinas,
        'turma_selecionada_id': int(turma_id) if turma_id else None,
        'disciplina_atual': disciplina_selecionada,
        'plano': plano,
        'dados_kanban': dados_kanban
    })

# API para Mover Card (Drag & Drop lógico)
@login_required
def mover_topico(request, id, novo_status):
    topico = get_object_or_404(TopicoPlano, id=id)
    if novo_status in ['TODO', 'DOING', 'DONE']:
        topico.status = novo_status
        topico.save()
    return JsonResponse({'status': 'ok'})
@login_required
@require_POST
def toggle_topico(request, id):
    topico = get_object_or_404(TopicoPlano, id=id)
    topico.concluido = not topico.concluido
    topico.save()
    return JsonResponse({'status': 'ok', 'concluido': topico.concluido})

@login_required
def api_gerar_questao(request):
    disciplina_id = request.GET.get('disciplina_id')
    topico = request.GET.get('topico')
    dificuldade = request.GET.get('dificuldade')
    
    # Busca nomes para passar pro prompt
    disciplina = "Geral"
    if disciplina_id:
        disc_obj = Disciplina.objects.filter(id=disciplina_id).first()
        if disc_obj: disciplina = disc_obj.nome

    # Busca habilidade selecionada (se houver)
    descritor_cod = request.GET.get('descritor') # Ex: "H1"
    habilidade_texto = "Foco em competências gerais"
    if descritor_cod:
        # Tenta achar a descrição no banco para ajudar a IA
        desc = Descritor.objects.filter(codigo=descritor_cod).first()
        if desc: habilidade_texto = f"{desc.codigo} - {desc.descricao}"

    # Chama a IA
    dados_ia = gerar_questao_ia(disciplina, topico, habilidade_texto, dificuldade)
    
    return JsonResponse(dados_ia)

@login_required
def gerenciar_descritores(request):
    # LÓGICA DE FILTROS (GET)
    filtro_disc = request.GET.get('disciplina')
    filtro_fonte = request.GET.get('fonte') # 'ENEM' ou 'SAEB'

    # Começa pegando todas as disciplinas
    disciplinas_queryset = Disciplina.objects.all().order_by('nome')

    # Se filtrou por disciplina, reduz o queryset principal
    if filtro_disc:
        disciplinas_queryset = disciplinas_queryset.filter(id=filtro_disc)

    # Prepara a lista final com pré-carregamento filtrado dos descritores
    # Isso é Python avançado: filtramos a sub-lista (descritores) dentro da lista principal (disciplinas)
    from django.db.models import Prefetch
    
    descritores_filter = Descritor.objects.all().order_by('codigo')
    if filtro_fonte:
        if filtro_fonte == 'ENEM':
            descritores_filter = descritores_filter.filter(tema__icontains='ENEM')
        elif filtro_fonte == 'SAEB':
            descritores_filter = descritores_filter.exclude(tema__icontains='ENEM')
            
    disciplinas = disciplinas_queryset.prefetch_related(
        Prefetch('descritor_set', queryset=descritores_filter)
    )

    # --- LÓGICA DE SALVAR/EXCLUIR (POST) ---
    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'excluir':
            desc_id = request.POST.get('descritor_id')
            Descritor.objects.filter(id=desc_id).delete()
            messages.success(request, 'Removido com sucesso.')
        elif acao == 'salvar':
            # (Código de salvar igual ao anterior...)
            desc_id = request.POST.get('descritor_id')
            disciplina_id = request.POST.get('disciplina')
            codigo = request.POST.get('codigo')
            descricao = request.POST.get('descricao')
            tema = request.POST.get('tema')
            
            dados = {'disciplina_id': disciplina_id, 'codigo': codigo, 'descricao': descricao, 'tema': tema}
            if desc_id:
                d = Descritor.objects.get(id=desc_id)
                for k, v in dados.items(): setattr(d, k, v)
                d.save()
                messages.success(request, 'Atualizado!')
            else:
                Descritor.objects.create(**dados)
                messages.success(request, 'Criado!')
        return redirect('gerenciar_descritores')

    context = {
        'disciplinas': disciplinas,
        'todas_disciplinas': Disciplina.objects.all().order_by('nome'), # Para o select do filtro
        'filtro_atual_disc': int(filtro_disc) if filtro_disc else '',
        'filtro_atual_fonte': filtro_fonte or ''
    }
    return render(request, 'core/gerenciar_descritores.html', context)

def upload_correcao_cartao(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    if request.method == 'POST' and request.FILES.get('foto_cartao'):
        foto = request.FILES['foto_cartao']
        aluno_id = request.POST.get('aluno_id') # Você pode selecionar o aluno num dropdown antes
        
        # 1. Salva a foto temporariamente
        path = f"media/temp/{foto.name}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb+') as destination:
            for chunk in foto.chunks():
                destination.write(chunk)
        
        # 2. Roda o Scanner
        scanner = OMRScanner()
        # Ajuste qtd_questoes para bater com a prova
        qtd_q = ItemGabarito.objects.filter(avaliacao=avaliacao).count() or 10
        resultado = scanner.processar_cartao(path, qtd_questoes=qtd_q)
        
        # 3. Processa o Resultado
        if resultado['sucesso']:
            respostas = resultado['respostas'] # Ex: {1: 'A', 2: 'C'}
            aluno = get_object_or_404(Aluno, id=aluno_id)
            
            acertos = 0
            # Salva no banco (RespostaDetalhada)
            for num, letra_marcada in respostas.items():
                item_prova = ItemGabarito.objects.filter(avaliacao=avaliacao, numero=num).first()
                if item_prova:
                    acertou = (letra_marcada == item_prova.resposta_correta)
                    if acertou: acertos += 1
                    
                    RespostaDetalhada.objects.update_or_create(
                        resultado__aluno=aluno,
                        resultado__avaliacao=avaliacao,
                        questao=item_prova.questao_banco,
                        defaults={
                            'resposta_aluno': letra_marcada,
                            'acertou': acertou
                        }
                    )
            
            # Atualiza nota final
            ResultadoAvaliacao.objects.update_or_create(
                aluno=aluno,
                avaliacao=avaliacao,
                defaults={'nota': acertos, 'data_realizacao': datetime.now()}
            )
            
            messages.success(request, f"Cartão lido! Nota calculada: {acertos}")
        else:
            messages.error(request, f"Erro na leitura: {resultado.get('erro')}")
            
        # Limpa arquivo temp
        os.remove(path)
        return redirect('detalhes_avaliacao', avaliacao_id=avaliacao.id)

    # GET: Mostra formulário de upload
    alunos = Aluno.objects.filter(turma=avaliacao.turma)
    return render(request, 'core/professor/upload_cartao.html', {'avaliacao': avaliacao, 'alunos': alunos})

# ==========================================
# 1. API DE LEITURA (COM INTEGRAÇÃO QR CODE)
# ==========================================
@csrf_exempt 
def api_ler_cartao(request):
    if request.method == 'POST' and request.FILES.get('foto'):
        path = ""
        try:
            foto = request.FILES['foto']
            avaliacao_id = request.POST.get('avaliacao_id')
            
            # 1. Salva temporariamente
            path = f"media/temp/{foto.name}"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb+') as destination:
                for chunk in foto.chunks():
                    destination.write(chunk)

            # 2. Define questões (Padrão 10 ou busca do banco)
            qtd_questoes = 10
            if avaliacao_id:
                # Opcional: qtd_questoes = ItemGabarito.objects.filter(avaliacao_id=avaliacao_id).count() or 10
                pass

            # 3. Roda o Scanner
            scanner = OMRScanner()
            resultado = scanner.processar_cartao(path, qtd_questoes=qtd_questoes)
            
            # 4. Lógica de Identificação do Aluno pelo QR Code
            # O formato gerado no PDF é: "A{id}-U{id}" (Ex: A15-U102)
            if resultado.get('qr_code'):
                try:
                    codigo = resultado['qr_code'] # Ex: "A15-U102"
                    partes = codigo.split('-') # ['A15', 'U102']
                    
                    for p in partes:
                        if p.startswith('U'):
                            # Remove o 'U' e pega o ID (Ex: 102)
                            aluno_id = int(p[1:])
                            resultado['aluno_detectado_id'] = aluno_id
                            
                        # Opcional: Verificar se a prova é a correta
                        # if p.startswith('A') and int(p[1:]) != int(avaliacao_id):
                        #     resultado['aviso'] = "Atenção: O QR Code indica uma prova diferente!"
                except Exception as e:
                    print(f"Erro ao parsear QR Code: {e}")

            # 5. Apaga o arquivo temp
            if os.path.exists(path):
                os.remove(path)

            return JsonResponse(resultado)

        except Exception as e:
            # Garante limpeza em caso de erro
            if os.path.exists(path):
                os.remove(path)
            return JsonResponse({'sucesso': False, 'erro': str(e)})

    return JsonResponse({'sucesso': False, 'erro': 'Nenhuma imagem enviada'})

def central_ajuda(request):
    # Se for professor/admin logado
    if request.user.is_authenticated and request.user.is_staff:
        tutoriais = Tutorial.objects.filter(publico__in=['PROF', 'TODOS'])
        publico_alvo = "Professor"
    else:
        # Se for aluno (mesmo sem login ou login simples)
        tutoriais = Tutorial.objects.filter(publico__in=['ALUNO', 'TODOS'])
        publico_alvo = "Estudante"

    categorias = CategoriaAjuda.objects.all()
    
    # Organiza por categoria
    conteudo_por_cat = {}
    for cat in categorias:
        tuts = tutoriais.filter(categoria=cat)
        if tuts.exists():
            conteudo_por_cat[cat] = tuts

    return render(request, 'core/ajuda.html', {
        'conteudo': conteudo_por_cat,
        'publico': publico_alvo
    })


@login_required
def dashboard_aluno(request):
    # 1. Segurança e Identificação
    try:
        aluno = request.user.aluno
    except:
        return redirect('dashboard')

    # 2. Busca Resultados Gerais
    resultados = Resultado.objects.filter(aluno=aluno).order_by('-avaliacao__data_aplicacao')

    # 3. Cálculo da Média Geral
    media_geral = 0
    if resultados.exists():
        # Filtra apenas quem tem percentual calculado
        notas_validas = [r.percentual for r in resultados if r.percentual is not None]
        if notas_validas:
            media_geral = sum(notas_validas) / len(notas_validas)

    # ==========================================================
    # 4. ALGORITMO DE RAIO-X (DESCRIÇÃO DE HABILIDADES)
    # ==========================================================
    # Vamos pegar todas as respostas detalhadas desse aluno
    respostas = RespostaDetalhada.objects.filter(resultado__aluno=aluno)
    
    # Dicionário para agrupar: { 'D12': {'acertos': 5, 'total': 10, 'obj': Descritor} }
    analise_descritores = {}

    for resp in respostas:
        # Pega a questão e o descritor associado (se houver)
        if resp.questao and resp.questao.descritor:
            desc = resp.questao.descritor
            cod = desc.codigo
            
            if cod not in analise_descritores:
                analise_descritores[cod] = {
                    'codigo': cod,
                    'descricao': desc.descricao,
                    'total': 0,
                    'acertos': 0
                }
            
            analise_descritores[cod]['total'] += 1
            if resp.acertou:
                analise_descritores[cod]['acertos'] += 1

    # Calcula a porcentagem de cada descritor e converte para lista
    lista_habilidades = []
    for cod, dados in analise_descritores.items():
        porcentagem = (dados['acertos'] / dados['total']) * 100
        dados['porcentagem'] = porcentagem
        lista_habilidades.append(dados)

    # Ordena: Os melhores primeiro
    lista_habilidades.sort(key=lambda x: x['porcentagem'], reverse=True)

    # Separa o Top 3 Melhores e Top 3 Piores
    pontos_fortes = [h for h in lista_habilidades if h['porcentagem'] >= 70][:3]
    pontos_atencao = [h for h in lista_habilidades if h['porcentagem'] < 50]
    # Pega os 3 piores dos pontos de atenção (revertendo a ordem para pegar os menores)
    pontos_atencao.sort(key=lambda x: x['porcentagem']) 
    pontos_atencao = pontos_atencao[:3]

    context = {
        'aluno': aluno,
        'resultados': resultados,
        'media_geral': media_geral,
        'total_provas': resultados.count(),
        'pontos_fortes': pontos_fortes,
        'pontos_atencao': pontos_atencao,
    }
    
    return render(request, 'core/dashboard_aluno.html', context)

@login_required
def dashboard_redirect(request):
    # 1. Se for Aluno
    if hasattr(request.user, 'aluno'):
        return dashboard_aluno(request)

    # 2. Se for Professor/Staff
    elif request.user.is_staff:
        # CORREÇÃO: Troque 'return index(request)' por 'return dashboard(request)'
        return dashboard(request) 
    
    # 3. Se não for nada
    else:
        return HttpResponse("Acesso não autorizado.")
    

# core/views.py

def consultar_acesso(request):
    resultado = None
    if request.method == 'POST':
        termo = request.POST.get('nome_busca')
        # Busca alunos que contenham o nome digitado (case insensitive)
        if termo:
            resultado = Aluno.objects.filter(nome_completo__icontains=termo, ativo=True)
    
    return render(request, 'core/consultar_acesso.html', {'resultado': resultado})

def logout_view(request):
    logout(request) # Desloga o usuário
    return redirect('login') # Manda de volta pra tela de login


@login_required
def trocar_senha_aluno(request):
    if request.method == 'POST':
        nova_senha = request.POST.get('nova_senha')
        confirmacao = request.POST.get('confirmacao_senha')
        
        # Validações básicas
        if not nova_senha or len(nova_senha) < 6:
            messages.error(request, 'A senha deve ter pelo menos 6 caracteres.')
            return redirect('dashboard_aluno')
            
        if nova_senha != confirmacao:
            messages.error(request, 'As senhas não conferem.')
            return redirect('dashboard_aluno')
            
        # Salva a nova senha
        u = request.user
        u.set_password(nova_senha)
        u.save()
        
        # Mantém o usuário logado (senão o Django desloga ao mudar senha)
        update_session_auth_hash(request, u)
        
        messages.success(request, 'Senha alterada com sucesso! Não esqueça a nova senha.')
        
    return redirect('dashboard_aluno')