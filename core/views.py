import io
import os
import json
import csv
import qrcode
import unicodedata
import pandas as pd
from xhtml2pdf import pisa
from random import shuffle
from datetime import datetime
from io import StringIO, BytesIO

# Django Imports
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Sum, Q, F, Prefetch
from django.db import transaction
from django.db.models.functions import Coalesce
from django.http import FileResponse, JsonResponse, HttpResponse
from django.utils import timezone
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
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader

# Seus Modelos e Forms
from .models import (
    Turma, Resultado, Avaliacao, Questao, Aluno, Disciplina, 
    RespostaDetalhada, ItemGabarito, Descritor, NDI, PlanoEnsino,
    TopicoPlano, ConfiguracaoSistema, Tutorial, CategoriaAjuda, Matricula
)
from .forms import (
    AvaliacaoForm, ResultadoForm, GerarProvaForm, ImportarQuestoesForm, 
    DefinirGabaritoForm, ImportarAlunosForm, AlunoForm
)

from .services.ai_generator import gerar_questao_ia
from .services.omr_scanner import OMRScanner

# ==============================================================================
# 🖨️ FUNÇÕES AUXILIARES DE PDF (LAYOUT)
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
            logo_img = ImageReader(config.logo.path)
            p.drawImage(logo_img, 40, 760, width=60, height=60, mask='auto', preserveAspectRatio=True)
            offset_x = 70 
        except:
            pass

    # Nome da Escola 
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
# 📊 DASHBOARD OTIMIZADO 2.0 (JÁ CORRIGIDO ANTERIORMENTE)
# ==============================================================================

@login_required
def dashboard(request):
    import json
    from django.db.models import Avg, Count, Q
    from django.db.models.functions import Coalesce

    # --- 1. FILTROS ---
    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    
    # Base: Resultados
    resultados = Resultado.objects.all()

    if disciplina_id: resultados = resultados.filter(avaliacao__disciplina_id=disciplina_id)
    if serie_id: resultados = resultados.filter(avaliacao__turma__nome__startswith=serie_id)
    if turma_id: resultados = resultados.filter(avaliacao__turma_id=turma_id)
    if aluno_id: resultados = resultados.filter(matricula__aluno_id=aluno_id)
    if avaliacao_id: resultados = resultados.filter(avaliacao_id=avaliacao_id)
    if data_inicio: resultados = resultados.filter(avaliacao__data_aplicacao__gte=data_inicio)
    if data_fim: resultados = resultados.filter(avaliacao__data_aplicacao__lte=data_fim)

    # --- 2. PROCESSAMENTO OTIMIZADO ---

    # A. KPI & PIZZA
    kpis = resultados.aggregate(
        total=Count('id'),
        media=Avg('percentual'),
        avancado=Count('id', filter=Q(percentual__gte=90)),
        adequado=Count('id', filter=Q(percentual__gte=70, percentual__lt=90)),
        intermediario=Count('id', filter=Q(percentual__gte=50, percentual__lt=70)),
        critico=Count('id', filter=Q(percentual__lt=50))
    )

    count_avaliados = kpis['total']
    media_geral = round((kpis['media'] or 0) / 10, 1)
    
    dados_pizza = [kpis['avancado'], kpis['adequado'], kpis['intermediario'], kpis['critico']]

    nivel_predominante = "-"
    if count_avaliados > 0:
        idx_max = dados_pizza.index(max(dados_pizza))
        nomes = ["Avançado 🚀", "Adequado ✅", "Intermediário ⚠️", "Crítico 🚨"]
        nivel_predominante = nomes[idx_max]

    qtd_provas = resultados.values('avaliacao').distinct().count()

    # Detalhes Pizza (Limitado a 500 para não travar)
    detalhes_qs = resultados.select_related('matricula__aluno', 'matricula__turma').only(
        'percentual', 'matricula__aluno__nome_completo', 'matricula__turma__nome'
    )[:500]

    detalhes_pizza = {'Avançado': [], 'Adequado': [], 'Intermediário': [], 'Crítico': []}
    for res in detalhes_qs:
        p = float(res.percentual or 0)
        info = {'nome': res.matricula.aluno.nome_completo, 'turma': res.matricula.turma.nome, 'nota': round(p/10, 1)}
        if p >= 90: detalhes_pizza['Avançado'].append(info)
        elif p >= 70: detalhes_pizza['Adequado'].append(info)
        elif p >= 50: detalhes_pizza['Intermediário'].append(info)
        else: detalhes_pizza['Crítico'].append(info)
    
    detalhes_pizza_json = json.dumps(detalhes_pizza)

    # B. PROFICIÊNCIA POR DESCRITOR (A CORREÇÃO ESTÁ AQUI)
    respostas_base = RespostaDetalhada.objects.filter(resultado__in=resultados)
    
    # Coalesce: Pega do ItemGabarito. Se for nulo, pega da Questao.
    stats_desc = respostas_base.annotate(
        cod_final=Coalesce('item_gabarito__descritor__codigo', 'questao__descritor__codigo')
    ).values('cod_final').annotate(
        total=Count('id'),
        acertos=Count('id', filter=Q(acertou=True))
    ).order_by('cod_final')

    labels_proficiencia = []
    dados_proficiencia = []
    
    for item in stats_desc:
        cod = item['cod_final']
        if cod: 
            perc = (item['acertos'] / item['total']) * 100 if item['total'] > 0 else 0
            labels_proficiencia.append(cod)
            dados_proficiencia.append(round(perc, 1))

    # C. RANKING DE QUESTÕES (CORRIGIDO TAMBÉM)
    ranking_q = respostas_base.annotate(
        desc_final=Coalesce('item_gabarito__descritor__codigo', 'questao__descritor__codigo')
    ).values(
        'desc_final',
        'questao__enunciado',
        'item_gabarito__questao_banco__enunciado',
        'item_gabarito__numero'
    ).annotate(
        total=Count('id'),
        acertos=Count('id', filter=Q(acertou=True))
    )

    lista_questoes = []
    for r in ranking_q:
        if r['total'] > 0:
            texto = r['questao__enunciado'] or r['item_gabarito__questao_banco__enunciado'] or f"Questão {r.get('item_gabarito__numero')}"
            desc = r['desc_final'] or "Geral"
            perc = (r['acertos'] / r['total']) * 100
            lista_questoes.append({
                'desc': desc, 'texto': texto[:100],
                'percentual_acerto': round(perc, 1),
                'percentual_erro': round(100 - perc, 1)
            })

    ranking_facil = sorted(lista_questoes, key=lambda x: x['percentual_acerto'], reverse=True)[:5]
    ranking_dificil = sorted(lista_questoes, key=lambda x: x['percentual_erro'], reverse=True)[:5]

    # D. EVOLUÇÃO
    evolucao_qs = resultados.values('avaliacao__titulo', 'avaliacao__data_aplicacao') \
                            .annotate(media=Avg('percentual')) \
                            .order_by('avaliacao__data_aplicacao')
    
    labels_evolucao = [e['avaliacao__data_aplicacao'].strftime('%d/%m') for e in evolucao_qs if e['avaliacao__data_aplicacao']]
    dados_evolucao = [round(e['media'], 1) for e in evolucao_qs if e['avaliacao__data_aplicacao']]

    # E. HEATMAP (Só carrega se filtrar prova)
    itens_heatmap = []
    matriz_calor = []
    
    if avaliacao_id:
        try:
            av = Avaliacao.objects.get(id=avaliacao_id)
            itens_heatmap = ItemGabarito.objects.filter(avaliacao=av).select_related('descritor').order_by('numero')
            res_heat = resultados.select_related('matricula__aluno').order_by('matricula__aluno__nome_completo')
            
            # Otimização extrema para Heatmap
            respostas_all = RespostaDetalhada.objects.filter(resultado__in=res_heat).values('resultado_id', 'item_gabarito_id', 'acertou')
            mapa_geral = {}
            for r in respostas_all:
                if r['resultado_id'] not in mapa_geral: mapa_geral[r['resultado_id']] = {}
                mapa_geral[r['resultado_id']][r['item_gabarito_id']] = r['acertou']

            for r in res_heat:
                mapa_aluno = mapa_geral.get(r.id, {})
                linha = {'aluno': r.matricula.aluno, 'nota': round((r.percentual or 0)/10, 1), 'questoes': []}
                for item in itens_heatmap:
                    linha['questoes'].append({'acertou': mapa_aluno.get(item.id), 'item': item})
                matriz_calor.append(linha)
        except: pass

    # Contexto
    turmas_filtro = Turma.objects.all().order_by('nome')
    if serie_id: turmas_filtro = turmas_filtro.filter(nome__startswith=serie_id)
    alunos_filtro = Aluno.objects.none()
    if turma_id: alunos_filtro = Aluno.objects.filter(matriculas__turma_id=turma_id, matriculas__status='CURSANDO')

    nome_filtro = "Visão Geral"
    if avaliacao_id: nome_filtro = "Prova Específica"
    elif turma_id: nome_filtro = "Turma Específica"

    context = {
        'serie_selecionada': serie_id, 'turma_selecionada': turma_id, 
        'aluno_selecionado': aluno_id, 'disciplina_selecionada': disciplina_id, 
        'avaliacao_selecionada': avaliacao_id, 'data_inicio': data_inicio, 'data_fim': data_fim,
        'turmas_da_serie': turmas_filtro, 'alunos_da_turma': alunos_filtro, 
        'disciplinas': Disciplina.objects.all().order_by('nome'), 
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-data_aplicacao')[:50],
        'nome_filtro': nome_filtro,
        'total_avaliacoes_contagem': count_avaliados,
        'media_geral': media_geral, 'nivel_predominante': nivel_predominante, 'qtd_provas': qtd_provas,
        'dados_pizza': dados_pizza, 'detalhes_pizza_json': detalhes_pizza_json,
        'labels_evolucao': labels_evolucao, 'dados_evolucao': dados_evolucao,
        'labels_proficiencia': labels_proficiencia, 'dados_proficiencia': dados_proficiencia,
        'ranking_facil': ranking_facil, 'ranking_dificil': ranking_dificil,
        'itens_heatmap': itens_heatmap, 'matriz_calor': matriz_calor,
    }

    return render(request, 'core/dashboard.html', context)

@login_required
def api_raio_x(request):
    descritor_cod = request.GET.get('descritor')
    
    # Filtros base
    filtros = Q(acertou=False) 
    
    # CORREÇÃO: Procura o descritor no Item OU na Questão
    if descritor_cod: 
        filtros &= (Q(item_gabarito__descritor__codigo=descritor_cod) | Q(questao__descritor__codigo=descritor_cod))
    
    # Filtros de contexto
    if request.GET.get('avaliacao'): filtros &= Q(resultado__avaliacao_id=request.GET.get('avaliacao'))
    if request.GET.get('turma'): filtros &= Q(resultado__avaliacao__turma_id=request.GET.get('turma'))
    if request.GET.get('serie'): filtros &= Q(resultado__avaliacao__turma__nome__startswith=request.GET.get('serie'))
    if request.GET.get('disciplina'): filtros &= Q(resultado__avaliacao__disciplina_id=request.GET.get('disciplina'))

    erros = RespostaDetalhada.objects.filter(filtros).values(
        'resultado__matricula__aluno__nome_completo',
        'resultado__matricula__turma__nome'
    ).distinct()[:200]

    lista_alunos = [
        f"{e['resultado__matricula__aluno__nome_completo']} <small class='text-muted'>({e['resultado__matricula__turma__nome']})</small>"
        for e in erros
    ]
    
    return JsonResponse({'alunos': lista_alunos})


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

                criados = 0
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
                        criados += 1
                    except Exception: pass

                msg_extra = f" (+{novas_disc} novas disciplinas e {descritores_novos} descritores)" if novas_disc > 0 else ""
                messages.success(request, f'Sucesso! {criados} questões importadas{msg_extra}.')
                return redirect('dashboard')
            except Exception as e:
                messages.error(request, f'Erro no arquivo: {str(e)}')
    else:
        form = ImportarQuestoesForm()
    return render(request, 'core/importar_questoes.html', {'form': form})

@login_required
def importar_alunos(request):
    # 1. BAIXAR MODELO
    if request.GET.get('baixar_modelo'):
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename=modelo_importacao_sami.xlsx'
        df_modelo = pd.DataFrame({
            'NOME COMPLETO': ['Nicolas Castro', 'Ana Souza'],
            'TURMA': ['3º Ano B', '1º Ano A']
        })
        df_modelo.to_excel(response, index=False)
        return response

    # 2. PROCESSAR UPLOAD
    if request.method == 'POST':
        form = ImportarAlunosForm(request.POST, request.FILES)
        if form.is_valid():
            erros_log = []  # Lista para guardar os erros
            try:
                arquivo = request.FILES['arquivo_excel']
                
                # Leitura flexível
                if arquivo.name.endswith('.csv'):
                    try:
                        df = pd.read_csv(arquivo, sep=';', encoding='utf-8')
                        if len(df.columns) < 2:
                            arquivo.seek(0)
                            df = pd.read_csv(arquivo, sep=',', encoding='utf-8')
                    except:
                        arquivo.seek(0)
                        df = pd.read_csv(arquivo, sep=';', encoding='latin-1')
                else:
                    df = pd.read_excel(arquivo)

                # Limpeza de colunas
                df.columns = [str(c).strip().upper() for c in df.columns]
                c_nome = next((c for c in df.columns if c in ['NOME', 'ESTUDANTE', 'ALUNO', 'NOME COMPLETO']), None)
                c_turma = next((c for c in df.columns if c in ['TURMA', 'CLASSE', 'SERIE']), None)

                if not c_nome:
                    messages.error(request, f"Erro: Coluna NOME não encontrada. Colunas lidas: {list(df.columns)}")
                    return redirect('importar_alunos')

                criados = 0
                
                for index, row in df.iterrows():
                    try:
                        raw_nome = row[c_nome]
                        if pd.isna(raw_nome) or str(raw_nome).strip() == '': continue
                        
                        # Tenta criar
                        turma_obj, _ = Turma.objects.get_or_create(
                            nome=str(row.get(c_turma, 'SEM TURMA')).strip().upper(),
                            defaults={'ano_letivo': 2026}
                        )
                        
                        aluno_obj, created_aluno = Aluno.objects.get_or_create(
                            nome_completo=str(raw_nome).strip().upper()
                        )
                        
                        Matricula.objects.get_or_create(
                            aluno=aluno_obj, turma=turma_obj, defaults={'status': 'CURSANDO'}
                        )
                        
                        if created_aluno: criados += 1

                    except Exception as e:
                        # Guarda o erro exato para mostrar na tela
                        erros_log.append(f"Linha {index}: {str(e)}")

                # FEEDBACK DETALHADO
                if criados > 0:
                    messages.success(request, f'✅ Sucesso! {criados} novos alunos importados.')
                elif erros_log:
                    # Mostra os 3 primeiros erros na tela
                    msg_erro = " | ".join(erros_log[:3])
                    messages.error(request, f'Falha ao salvar: {msg_erro}')
                else:
                    messages.warning(request, 'Nenhum aluno novo. Talvez já existam no banco?')

                return redirect('dashboard')

            except Exception as e:
                messages.error(request, f'Erro crítico no arquivo: {str(e)}')
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
        disciplina_id = request.POST.get('disciplina')
        data_aplicacao = request.POST.get('data_aplicacao')
        
        # Novos campos de alcance
        tipo_foco = request.POST.get('tipo_foco') # turma, serie, escola
        turma_id = request.POST.get('turma')
        serie_alvo = request.POST.get('serie_alvo')
        
        acao = request.POST.get('acao')
        modo = request.POST.get('modo_prova')

        if titulo and disciplina_id and data_aplicacao:
            try:
                turmas_alvo = []
                
                # 1. Define quem recebe a prova
                if tipo_foco == 'escola':
                    turmas_alvo = Turma.objects.all()
                elif tipo_foco == 'serie':
                    turmas_alvo = Turma.objects.filter(nome__startswith=serie_alvo)
                else: # turma específica
                    if turma_id:
                        turmas_alvo = [get_object_or_404(Turma, id=turma_id)]
                
                if not turmas_alvo:
                    messages.error(request, "Nenhuma turma selecionada.")
                    return redirect('criar_avaliacao')

                # 2. Criação em Massa
                count = 0
                ultimo_id = None
                
                with transaction.atomic():
                    for turma in turmas_alvo:
                        av = Avaliacao.objects.create(
                            titulo=titulo, 
                            turma=turma, 
                            disciplina_id=disciplina_id, 
                            data_aplicacao=data_aplicacao
                        )
                        ultimo_id = av.id
                        count += 1

                messages.success(request, f'Sucesso! {count} avaliações criadas.')

                # 3. Redirecionamento Inteligente
                # Se criou só uma e pediu para configurar, vai para a configuração
                if count == 1 and acao == 'salvar_configurar':
                    if modo == 'banco': 
                        return redirect('montar_prova', ultimo_id) 
                    else: 
                        return redirect('definir_gabarito', ultimo_id)
                
                # Se criou várias ou pediu para sair, volta para a lista
                return redirect('gerenciar_avaliacoes')

            except Exception as e:
                messages.error(request, f"Erro ao criar: {e}")
        else:
            messages.error(request, 'Erro: Preencha título, disciplina e data.')

    context = {
        'turmas': Turma.objects.filter(ano_letivo=2026).order_by('nome'), 
        'disciplinas': Disciplina.objects.all().order_by('nome')
    }
    return render(request, 'core/criar_avaliacao.html', context)

@login_required
def gerar_prova_pdf(request):
    """
    Gerador Inteligente 2.0 (Versão Completa & Definitiva):
    - Cria prova baseada em erros ou aleatória.
    - Salva no banco vinculando ao Aluno (Recuperação) ou Turma.
    - Gera PDF com descritores e gabarito.
    """
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        disciplina_id = request.POST.get('disciplina')
        tipo_foco = request.POST.get('tipo_foco') 
        
        # Parâmetros
        aluno_id = request.POST.get('aluno_id')
        turma_id = request.POST.get('turma_id')
        serie_alvo = request.POST.get('serie_alvo')
        
        qtd_questoes = int(request.POST.get('qtd_questoes', 10))
        salvar_sistema = request.POST.get('salvar_sistema') == 'on'

        disciplina_obj = get_object_or_404(Disciplina, id=disciplina_id)
        
        # --- 1. DEFINIÇÃO DO ESCOPO ---
        turmas_alvo = []
        matricula_alvo = None
        filtro_erros = Q()

        if tipo_foco == 'aluno' and aluno_id:
            aluno_obj = Aluno.objects.get(id=aluno_id)
            # Pega a matrícula ativa do aluno
            matricula_alvo = Matricula.objects.filter(aluno=aluno_obj, status='CURSANDO').last()
            
            if matricula_alvo:
                turmas_alvo = [matricula_alvo.turma]
                # Filtra erros apenas deste aluno específico
                filtro_erros = Q(resultado__matricula=matricula_alvo)
            else:
                messages.error(request, "Aluno sem matrícula ativa.")
                return redirect('gerenciar_avaliacoes')

        elif tipo_foco == 'turma' and turma_id:
            t_obj = get_object_or_404(Turma, id=turma_id)
            turmas_alvo = [t_obj]
            filtro_erros = Q(resultado__matricula__turma=t_obj)

        elif tipo_foco == 'serie' and serie_alvo:
            turmas_alvo = Turma.objects.filter(nome__startswith=serie_alvo)
            filtro_erros = Q(resultado__matricula__turma__nome__startswith=serie_alvo)

        elif tipo_foco == 'escola':
            turmas_alvo = Turma.objects.all()
            filtro_erros = Q() 

        if not turmas_alvo:
            messages.error(request, "Nenhuma turma encontrada.")
            return redirect('gerenciar_avaliacoes')

        # --- 2. SELEÇÃO DE QUESTÕES ---
        # Busca questões onde houve erro (acertou=False)
        erros_query = RespostaDetalhada.objects.filter(
            acertou=False, 
            questao__disciplina=disciplina_obj
        ).filter(filtro_erros)

        # Identifica os 5 descritores com mais erros
        descritores_criticos = erros_query.values('questao__descritor').annotate(total_erros=Count('id')).order_by('-total_erros')[:5]
        ids_descritores = [item['questao__descritor'] for item in descritores_criticos if item['questao__descritor']]

        questoes_finais = []
        
        # A. Tenta preencher com questões dos descritores críticos
        if ids_descritores:
            pool_focado = list(Questao.objects.filter(disciplina=disciplina_obj, descritor__in=ids_descritores))
            shuffle(pool_focado)
            questoes_finais = pool_focado[:qtd_questoes]
        
        # B. Se faltar questão, completa com aleatórias da disciplina (Fallback)
        falta = qtd_questoes - len(questoes_finais)
        if falta > 0:
            ids_ja_usados = [q.id for q in questoes_finais]
            pool_geral = list(Questao.objects.filter(disciplina=disciplina_obj).exclude(id__in=ids_ja_usados))
            shuffle(pool_geral)
            questoes_finais += pool_geral[:falta]

        shuffle(questoes_finais)
        
        if not questoes_finais:
            messages.error(request, "Não há questões suficientes no banco para esta disciplina.")
            return redirect('gerenciar_avaliacoes')

        # --- 3. SALVAR NO BANCO (AGORA DO JEITO CERTO) ---
        if salvar_sistema:
            try:
                with transaction.atomic():
                    count = 0
                    for turma in turmas_alvo:
                        # Define título
                        titulo_final = f"RECUPERAÇÃO: {titulo}" if matricula_alvo else titulo
                        
                        # Cria a Avaliação com todos os campos corretos
                        nova_av = Avaliacao.objects.create(
                            titulo=titulo_final,
                            disciplina=disciplina_obj,
                            turma=turma,
                            matricula=matricula_alvo,  # <--- AGORA O BANCO ACEITA ISSO!
                            data_aplicacao=datetime.now().date()
                        )
                        nova_av.questoes.set(questoes_finais)
                        
                        # Gera o gabarito oficial
                        for i, q in enumerate(questoes_finais, 1):
                            ItemGabarito.objects.create(
                                avaliacao=nova_av, numero=i, questao_banco=q,
                                resposta_correta=q.gabarito, descritor=q.descritor
                            )
                        count += 1
                    
                    messages.success(request, f"Avaliação salva com sucesso!")

            except Exception as e:
                messages.error(request, f"Erro técnico ao salvar: {e}")
                return redirect('gerenciar_avaliacoes')

        # --- 4. GERAÇÃO DO PDF ---
        # Se for para várias turmas, não gera PDF direto, redireciona.
        if len(turmas_alvo) > 1:
            return redirect('gerenciar_avaliacoes')
        
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        
        # Cabeçalho Personalizado
        nome_aluno_pdf = matricula_alvo.aluno.nome_completo if matricula_alvo else "___________________________________"
        
        # Função auxiliar de desenho (já existente no seu código)
        # Adaptamos o cabeçalho para mostrar o nome se for recuperação
        desenhar_cabecalho_prova(p, titulo, turmas_alvo[0].nome, disciplina_obj.nome)
        
        # Se for recuperação, escreve o nome do aluno já impresso
        if matricula_alvo:
            p.setFont("Helvetica-Bold", 10)
            p.setFillColor(colors.black)
            p.drawString(95, 775, nome_aluno_pdf) # Preenche a linha do nome

        y = 730
        for i, q in enumerate(questoes_finais, 1):
            # Enunciado
            p.setFont("Helvetica-Bold", 11)
            p.setFillColor(colors.black)
            texto_completo = f"{i}. {q.enunciado}"
            linhas_enunciado = simpleSplit(texto_completo, "Helvetica-Bold", 11, 480)
            
            # Cálculo de espaço
            espaco = (len(linhas_enunciado) * 15) + 120
            if q.imagem: espaco += 150
            espaco += 20 # Espaço descritor

            # Quebra de página
            if y - espaco < 50:
                p.showPage()
                desenhar_cabecalho_prova(p, titulo, turmas_alvo[0].nome, disciplina_obj.nome)
                if matricula_alvo: p.drawString(95, 775, nome_aluno_pdf)
                y = 730
            
            # Imprime texto
            for linha in linhas_enunciado:
                p.drawString(40, y, linha)
                y -= 15

            # Imprime Imagem
            if q.imagem:
                try:
                    img_reader = ImageReader(q.imagem.path)
                    iw, ih = img_reader.getSize()
                    aspect = ih / float(iw)
                    h_img = 200 * aspect
                    p.drawImage(img_reader, 50, y - h_img, width=200, height=h_img)
                    y -= (h_img + 10)
                except: pass

            # Imprime Alternativas
            p.setFont("Helvetica", 10)
            opts = [('A', q.alternativa_a), ('B', q.alternativa_b), ('C', q.alternativa_c), ('D', q.alternativa_d)]
            if q.alternativa_e: opts.append(('E', q.alternativa_e))
            
            for l, txt in opts:
                if txt:
                    p.drawString(50, y, f"{l}) {txt}")
                    y -= 15
            
            # Imprime Descritor (Rodapé da questão)
            if q.descritor:
                p.setFont("Helvetica-Oblique", 8) 
                p.setFillColorRGB(0.4, 0.4, 0.4) 
                
                txt_desc = f"Habilidade: {q.descritor.codigo} - {q.descritor.descricao[:90]}"
                if len(q.descritor.descricao) > 90: txt_desc += "..."
                p.drawString(50, y, txt_desc)
                p.setFillColorRGB(0, 0, 0) 
                y -= 15 
            
            y -= 20 

        # --- PÁGINA FINAL: GABARITO ---
        p.showPage()
        
        p.setFont("Helvetica-Bold", 16)
        p.drawCentredString(300, 800, "GABARITO DO PROFESSOR")
        p.setFont("Helvetica", 10)
        p.drawCentredString(300, 780, f"Prova: {titulo} | Disciplina: {disciplina_obj.nome}")
        if matricula_alvo:
            p.drawCentredString(300, 765, f"Aluno(a): {matricula_alvo.aluno.nome_completo}")
        
        y = 740
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Questão")
        p.drawString(120, y, "Resp.")
        p.drawString(200, y, "Descritor / Habilidade")
        p.setLineWidth(1)
        p.line(40, y-5, 550, y-5)
        y -= 20
        
        p.setFont("Helvetica", 10)
        for i, q in enumerate(questoes_finais, 1):
            p.drawString(65, y, str(i).zfill(2))
            
            p.circle(135, y+3, 8, stroke=1, fill=0) 
            p.drawCentredString(135, y, q.gabarito)
            
            desc_texto = "Geral"
            if q.descritor:
                desc_texto = f"{q.descritor.codigo} - {q.descritor.descricao}"
            
            p.drawString(200, y, desc_texto[:65])
            
            y -= 20
            if y < 50:
                p.showPage()
                p.setFont("Helvetica-Bold", 10)
                p.drawString(40, 800, "Continuação do Gabarito")
                y = 760

        p.save()
        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f'Prova_{titulo}.pdf')

    return redirect('gerenciar_avaliacoes')


@login_required
def baixar_prova_existente(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    itens = ItemGabarito.objects.filter(avaliacao=avaliacao, questao_banco__isnull=False).select_related('questao_banco', 'descritor').order_by('numero')

    if not itens.exists():
        messages.error(request, "Esta avaliação não possui questões do banco vinculadas.")
        return redirect('gerenciar_avaliacoes')

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    
    desenhar_cabecalho_prova(p, avaliacao.titulo, avaliacao.turma.nome, avaliacao.disciplina.nome)
    
    y = 730 
    
    for i, item in enumerate(itens, 1):
        q = item.questao_banco
        
        p.setFont("Helvetica-Bold", 11)
        texto_completo = f"{item.numero}. {q.enunciado}"
        linhas_enunciado = simpleSplit(texto_completo, "Helvetica-Bold", 11, 480)
        
        espaco_necessario = (len(linhas_enunciado) * 15) + 140 
        if q.imagem: espaco_necessario += 150 

        if y - espaco_necessario < 50:
            p.showPage()
            p.setFont("Helvetica-Bold", 10)
            p.drawString(40, 800, f"Continuação - {avaliacao.titulo}")
            p.line(40, 790, 550, 790)
            y = 760
        
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
                    y = 760
                
                y -= display_height
                p.drawImage(img_path, 50, y, width=display_width, height=display_height)
                y -= 10
            except: pass

        p.setFont("Helvetica-Oblique", 8)
        p.setFillColorRGB(0.4, 0.4, 0.4)
        
        desc = item.descritor if item.descritor else q.descritor
        desc_texto = "Habilidade: Geral"
        if desc:
            desc_texto = f"Habilidade: {desc.codigo} - {desc.descricao[:70]}..."
            
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

    p.showPage() 
    
    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(300, 800, "GABARITO DO PROFESSOR")
    p.setFont("Helvetica", 10)
    p.drawCentredString(300, 780, f"Prova: {avaliacao.titulo} | Data: {avaliacao.data_aplicacao.strftime('%d/%m/%Y')}")
    
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
        p.circle(140, y+3, 8, stroke=1, fill=0) 
        p.drawCentredString(140, y, item.resposta_correta)
        
        desc_cod = "Geral"
        desc_item = item.descritor if item.descritor else item.questao_banco.descritor
        if desc_item:
            desc_cod = f"{desc_item.codigo} - {desc_item.tema if desc_item.tema else ''}"
            
        p.drawString(200, y, desc_cod[:50]) 
        
        y -= 20
        if y < 50:
            p.showPage()
            y = 800

    p.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'Prova_{avaliacao.titulo}.pdf')

@login_required
def montar_prova(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
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

    questoes = Questao.objects.filter(disciplina=avaliacao.disciplina).order_by('-id')
    
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
        questoes = questões.filter(enunciado__icontains=f_busca)

    descritores = Descritor.objects.filter(disciplina=avaliacao.disciplina).order_by('codigo')

    context = {
        'avaliacao': avaliacao,
        'questoes': questoes,
        'descritores': descritores,
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
    
    # Auto-preencher se vier do banco de questões (Primeiro acesso)
    if not itens_salvos.exists() and avaliacao.questoes.exists():
        for i, q in enumerate(avaliacao.questoes.all(), 1):
            ItemGabarito.objects.create(
                avaliacao=avaliacao, numero=i, questao_banco=q,
                resposta_correta=q.gabarito, descritor=q.descritor
            )
        messages.success(request, "Gabarito importado das questões do banco!")
        return redirect('definir_gabarito', avaliacao_id=avaliacao.id)

    if request.method == 'POST':
        # CASO 1: Definir quantidade inicial (Grade Vazia)
        if 'qtd_questoes' in request.POST:
            qtd = int(request.POST.get('qtd_questoes'))
            ItemGabarito.objects.filter(avaliacao=avaliacao).delete()
            # Tenta pegar um descritor padrão só pra não ir vazio
            desc_padrao = Descritor.objects.filter(disciplina=avaliacao.disciplina).first()
            
            for i in range(1, qtd + 1):
                ItemGabarito.objects.create(
                    avaliacao=avaliacao, numero=i, resposta_correta='A', descritor=desc_padrao
                )
            return redirect('definir_gabarito', avaliacao_id=avaliacao.id)
        
        # CASO 2: Salvar Alterações e Replicar
        else:
            try:
                with transaction.atomic():
                    # 1. Salva a prova ATUAL
                    for item in itens_salvos:
                        nova_resposta = request.POST.get(f'resposta_{item.id}')
                        novo_descritor_id = request.POST.get(f'descritor_{item.id}')
                        
                        if nova_resposta: item.resposta_correta = nova_resposta
                        if novo_descritor_id: item.descritor_id = novo_descritor_id
                        item.save()

                    # 2. Verifica se deve REPLICAR para as outras turmas
                    if request.POST.get('replicar_para_todos') == 'on':
                        # Busca provas "irmãs" (Mesmo título e disciplina, mas turmas diferentes)
                        provas_irmas = Avaliacao.objects.filter(
                            titulo=avaliacao.titulo, 
                            disciplina=avaliacao.disciplina
                        ).exclude(id=avaliacao.id)

                        count_replicas = 0
                        
                        for irma in provas_irmas:
                            # Limpa gabarito antigo da irmã
                            ItemGabarito.objects.filter(avaliacao=irma).delete()
                            
                            # Cria cópias exatas dos itens da atual
                            novos_itens = []
                            for gabarito_oficial in itens_salvos:
                                novos_itens.append(ItemGabarito(
                                    avaliacao=irma,
                                    numero=gabarito_oficial.numero,
                                    resposta_correta=gabarito_oficial.resposta_correta,
                                    descritor=gabarito_oficial.descritor,
                                    questao_banco=gabarito_oficial.questao_banco
                                ))
                            ItemGabarito.objects.bulk_create(novos_itens)
                            count_replicas += 1
                        
                        messages.success(request, f"Gabarito salvo e replicado para outras {count_replicas} turmas com sucesso!")
                    else:
                        messages.success(request, "Gabarito salvo apenas para esta turma.")

            except Exception as e:
                messages.error(request, f"Erro ao salvar: {e}")

            return redirect('gerenciar_avaliacoes')

    # Busca descritores para o Select
    descritores = Descritor.objects.filter(disciplina=avaliacao.disciplina).order_by('codigo')

    context = {
        'avaliacao': avaliacao, 
        'itens': itens_salvos,
        'descritores': descritores, 
        'tem_itens': itens_salvos.exists()
    }
    return render(request, 'core/definir_gabarito.html', context)

@login_required
def lancar_nota(request):
    avaliacao_id = request.GET.get('avaliacao_id')
    avaliacao_obj = None
    itens = []
    matriculas_turma = []

    if avaliacao_id:
        avaliacao_obj = get_object_or_404(Avaliacao, id=avaliacao_id)
        itens = ItemGabarito.objects.filter(avaliacao=avaliacao_obj).order_by('numero')
        
        # --- AQUI ESTÁ A CORREÇÃO INTELIGENTE ---
        if avaliacao_obj.matricula:
            # CASO 1: Prova Individual (Recuperação)
            # Traz APENAS a matrícula do aluno dono da prova
            matriculas_turma = Matricula.objects.filter(id=avaliacao_obj.matricula.id)
        else:
            # CASO 2: Prova da Turma (Geral)
            # Traz todos os alunos ativos daquela turma
            matriculas_turma = Matricula.objects.filter(
                turma=avaliacao_obj.turma, 
                status='CURSANDO'
            ).select_related('aluno').order_by('aluno__nome_completo')
        # ----------------------------------------

    if request.method == 'POST' and avaliacao_obj:
        matricula_id = request.POST.get('aluno') # ID da matrícula
        
        if not matricula_id:
            messages.error(request, "Selecione um aluno.")
            return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

        # Busca a matrícula (Garante que existe e pertence à turma ou é o dono)
        matricula_obj = get_object_or_404(Matricula, id=matricula_id)
        
        # Cria ou pega o resultado (garantindo valores iniciais para não dar erro de conta)
        resultado = Resultado.objects.filter(avaliacao=avaliacao_obj, matricula=matricula_obj).first()

        if not resultado:
            resultado = Resultado(
                avaliacao=avaliacao_obj,
                matricula=matricula_obj,
                total_questoes=itens.count(),
                acertos=0,
                percentual=0.0
            )
            resultado.save()
        else:
            resultado.total_questoes = itens.count()
            if resultado.acertos is None: resultado.acertos = 0
            if resultado.percentual is None: resultado.percentual = 0.0
            resultado.save()

        # Limpa respostas antigas para regravar
        RespostaDetalhada.objects.filter(resultado=resultado).delete()
        
        acertos_contagem = 0
        objs_resposta = []
        
        for item in itens:
            # Pega resposta do formulário (Suporta name="resposta_15" ou name="resposta_q1")
            resp_via_id = request.POST.get(f'resposta_{item.id}')
            resp_via_num = request.POST.get(f'resposta_q{item.numero}')
            resposta_aluno = resp_via_id if resp_via_id else resp_via_num
            
            acertou = False
            letra_final = ''

            if resposta_aluno:
                letra_final = resposta_aluno.strip().upper()
                if letra_final == item.resposta_correta.upper():
                    acertou = True
                    acertos_contagem += 1
                
                objs_resposta.append(RespostaDetalhada(
                    resultado=resultado, item_gabarito=item,
                    questao=item.questao_banco, acertou=acertou,
                    resposta_aluno=letra_final
                ))

        if objs_resposta:
            RespostaDetalhada.objects.bulk_create(objs_resposta)

        # Lógica de Ausente (Opcional, vindo do JS)
        if request.POST.get('ausente') == 'true':
            resultado.acertos = 0
            resultado.percentual = 0.0
        else:
            resultado.acertos = acertos_contagem
            qtd = resultado.total_questoes if resultado.total_questoes > 0 else 1
            resultado.percentual = (acertos_contagem / qtd) * 100
        
        resultado.save()
        
        # Se for Ajax (Scanner), retorna JSON
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
             from django.http import JsonResponse
             return JsonResponse({'sucesso': True, 'msg': f'Nota salva: {acertos_contagem}'})

        messages.success(request, f'Nota salva: {acertos_contagem}')
        return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

    return render(request, 'core/lancar_nota.html', {
        'avaliacao_selecionada': avaliacao_obj,
        'itens': itens, 
        'matriculas': matriculas_turma, # Agora filtrado corretamente!
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-data_aplicacao')
    })

# ==============================================================================
# 📋 GERENCIAMENTO GERAL
# ==============================================================================

# Em core/views.py

@login_required
def gerenciar_alunos(request):
    # --- LÓGICA DE AÇÕES (POST) ---
    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        # 1. CRIAR NOVO ALUNO + MATRÍCULA
        if acao == 'criar':
            nome = request.POST.get('nome')
            turma_id = request.POST.get('turma')
            
            # Novos Campos de Inclusão e Social
            is_pcd = request.POST.get('is_pcd') == 'on'
            tipo_deficiencia = request.POST.get('tipo_deficiencia')
            cor_raca = request.POST.get('cor_raca')
            
            if nome and turma_id:
                try:
                    with transaction.atomic():
                        # Passo A: Cria o Aluno com os dados novos
                        novo_aluno = Aluno.objects.create(
                            nome_completo=nome.upper(),
                            is_pcd=is_pcd,
                            tipo_deficiencia=tipo_deficiencia,
                            cor_raca=cor_raca
                        )
                        
                        # Passo B: Cria a Matrícula
                        turma_obj = Turma.objects.get(id=turma_id)
                        Matricula.objects.create(aluno=novo_aluno, turma=turma_obj, status='CURSANDO')
                        
                        msg_extra = " (Marcado como Inclusão)" if is_pcd else ""
                        messages.success(request, f'Aluno matriculado com sucesso!{msg_extra}')
                except Exception as e:
                    messages.error(request, f'Erro ao cadastrar: {e}')
            else:
                messages.error(request, 'Preencha nome e turma.')

        # 2. EDITAR ALUNO EXISTENTE
        elif acao == 'editar':
            matricula_id = request.POST.get('matricula_id')
            try:
                mat = get_object_or_404(Matricula, id=matricula_id)
                
                # Atualiza Dados Básicos
                novo_nome = request.POST.get('nome')
                if novo_nome: mat.aluno.nome_completo = novo_nome.upper()
                
                # Atualiza Dados de Inclusão e Social
                mat.aluno.is_pcd = request.POST.get('is_pcd') == 'on'
                mat.aluno.tipo_deficiencia = request.POST.get('tipo_deficiencia')
                mat.aluno.cor_raca = request.POST.get('cor_raca')
                mat.aluno.genero = request.POST.get('genero')
                mat.aluno.renda_familiar = request.POST.get('renda_familiar')
                
                mat.aluno.save() # Salva na tabela Aluno
                
                # Atualiza Turma (Tabela Matrícula)
                nova_turma_id = request.POST.get('turma')
                if nova_turma_id and nova_turma_id != str(mat.turma.id):
                    mat.turma = Turma.objects.get(id=nova_turma_id)
                
                # Atualiza Status
                status_novo = request.POST.get('status')
                if status_novo: mat.status = status_novo
                    
                mat.save() # Salva na tabela Matrícula
                messages.success(request, 'Dados do aluno atualizados com sucesso!')
            except Exception as e:
                messages.error(request, f'Erro ao editar: {e}')

        # 3. EXCLUIR (Inativar Matrícula ou Deletar)
        elif acao == 'excluir':
            matricula_id = request.POST.get('matricula_id')
            try:
                # Opção A: Deletar tudo (Aluno e Matrícula)
                mat = get_object_or_404(Matricula, id=matricula_id)
                aluno = mat.aluno
                mat.delete() # Apaga matrícula
                aluno.delete() # Apaga cadastro da pessoa
                messages.warning(request, 'Aluno e matrícula removidos.')
            except:
                messages.error(request, 'Erro ao excluir.')

        return redirect('gerenciar_alunos')

    # --- LÓGICA DE VISUALIZAÇÃO (GET) ---
    busca = request.GET.get('busca')
    filtro_turma = request.GET.get('turma')
    apenas_pcd = request.GET.get('apenas_pcd')
    
    # NOVO: Filtro de Ano (Padrão 2026)
    filtro_ano = request.GET.get('ano', '2026')

    # Busca matrículas. Filtra status 'CURSANDO' E O ANO LETIVO DA TURMA
    # Importante: se quiser ver alunos 'APROVADOS' de 2025, tem que tirar o status='CURSANDO' ou adaptar a lógica.
    # Aqui vou deixar mostrar todos daquele ano, independente do status, para você ver o histórico.
    matriculas = Matricula.objects.filter(turma__ano_letivo=filtro_ano).select_related('aluno', 'turma')
    
    # Se for o ano atual (2026), foca nos Cursando. Se for passado (2025), mostra tudo.
    if filtro_ano == '2026':
        matriculas = matriculas.filter(status='CURSANDO')

    # Filtros Adicionais
    if busca:
        matriculas = matriculas.filter(aluno__nome_completo__icontains=busca)
    
    if filtro_turma:
        matriculas = matriculas.filter(turma_id=filtro_turma)

    if apenas_pcd == 'on':
        matriculas = matriculas.filter(aluno__is_pcd=True)

    # Ordenação
    matriculas = matriculas.order_by('aluno__nome_completo')

    # Paginação
    paginator = Paginator(matriculas, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    # IMPORTANTE: O Select de turmas só deve mostrar turmas do ANO selecionado
    turmas_para_select = Turma.objects.filter(ano_letivo=filtro_ano).order_by('nome')

    return render(request, 'core/gerenciar_alunos.html', {
        'matriculas': page_obj,
        'turmas': turmas_para_select,
        'busca_atual': busca,
        'turma_selecionada': filtro_turma,
        'ano_atual': filtro_ano # Passa o ano para o template manter o filtro
    })

    # --- LÓGICA DE VISUALIZAÇÃO (GET) ---
    busca = request.GET.get('busca')
    filtro_turma = request.GET.get('turma')
    apenas_pcd = request.GET.get('apenas_pcd') # Novo Filtro

    # Busca matrículas ativas (CURSANDO) para exibir
    matriculas = Matricula.objects.filter(status='CURSANDO').select_related('aluno', 'turma')
    
    # Filtros
    if busca:
        matriculas = matriculas.filter(aluno__nome_completo__icontains=busca)
    
    if filtro_turma:
        matriculas = matriculas.filter(turma_id=filtro_turma)

    if apenas_pcd == 'on':
        matriculas = matriculas.filter(aluno__is_pcd=True)

    # Ordenação
    matriculas = matriculas.order_by('aluno__nome_completo')

    # Paginação
    paginator = Paginator(matriculas, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    turmas = Turma.objects.all().order_by('nome')

    return render(request, 'core/gerenciar_alunos.html', {
        'matriculas': page_obj,
        'turmas': turmas,
        'busca_atual': busca,
        'turma_selecionada': filtro_turma
    })

    # --- LÓGICA DE VISUALIZAÇÃO (GET) ---
    busca = request.GET.get('busca')
    filtro_turma = request.GET.get('turma')
    filtro_serie = request.GET.get('serie')
    ordem = request.GET.get('ordem', 'nome')

    # AQUI MUDOU: Trabalhamos com MATRÍCULAS agora
    matriculas = Matricula.objects.select_related('aluno', 'turma') \
        .annotate(media_geral=Avg('resultados__percentual'))
    
    # Filtros
    if busca:
        matriculas = matriculas.filter(
            Q(aluno__nome_completo__icontains=busca) | 
            Q(aluno__cpf__icontains=busca)
        )
    
    if filtro_turma:
        matriculas = matriculas.filter(turma_id=filtro_turma)
        
    if filtro_serie:
        matriculas = matriculas.filter(turma__nome__startswith=filtro_serie)

    # Ordenação
    if ordem == 'nome':
        matriculas = matriculas.order_by('aluno__nome_completo')
    elif ordem == 'melhores':
        matriculas = matriculas.order_by('-media_geral')
    elif ordem == 'criticos':
        matriculas = matriculas.order_by('media_geral')

    # Paginação
    paginator = Paginator(matriculas, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    turmas = Turma.objects.all().order_by('nome')

    return render(request, 'core/gerenciar_alunos.html', {
        'matriculas': page_obj, # Enviamos como 'matriculas' para ficar claro no HTML
        'turmas': turmas,
        'busca_atual': busca,
        'turma_selecionada': filtro_turma, # Corrigido para manter select marcado
        'serie_selecionada': filtro_serie,
        'ordem_atual': ordem
    })

@login_required
def gerenciar_avaliacoes(request):
    # Lógica de Exclusão (Mantida igual)
    if request.method == 'POST' and 'delete_id' in request.POST:
        av = get_object_or_404(Avaliacao, id=request.POST.get('delete_id'))
        av.delete()
        messages.success(request, 'Avaliação removida com sucesso!')
        return redirect('gerenciar_avaliacoes')

    # --- FILTROS COMPLETOS ---
    turma_id = request.GET.get('turma')
    disciplina_id = request.GET.get('disciplina')
    data_filtro = request.GET.get('data') # Novo campo data

    # Base da busca (Ordenado por data decrescente)
    avaliacoes = Avaliacao.objects.select_related('turma', 'disciplina').order_by('-data_aplicacao')
    
    if turma_id:
        avaliacoes = avaliacoes.filter(turma_id=turma_id)
    
    if disciplina_id:
        avaliacoes = avaliacoes.filter(disciplina_id=disciplina_id)
        
    if data_filtro:
        avaliacoes = avaliacoes.filter(data_aplicacao=data_filtro)

    context = {
        'avaliacoes': avaliacoes,
        'turmas': Turma.objects.all().order_by('nome'),
        'disciplinas': Disciplina.objects.all().order_by('nome'),
        'total_avaliacoes': avaliacoes.count(),
        
        # Devolvemos o valor selecionado para o HTML manter o filtro ativo
        'filtro_turma': int(turma_id) if turma_id else None,
        'filtro_disciplina': int(disciplina_id) if disciplina_id else None,
        'filtro_data': data_filtro
    }
    
    return render(request, 'core/avaliacoes.html', context)


@login_required
def gerenciar_turmas(request):
    from django.db.models import Count, Q

    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        # 1. CRIAR TURMA
        if acao == 'criar':
            nome = request.POST.get('nome_turma')
            # Pega o ano do select ou usa 2026 como padrão
            ano = request.POST.get('ano_letivo', 2026) 
            
            if nome:
                Turma.objects.create(nome=nome, ano_letivo=ano)
                messages.success(request, 'Turma criada com sucesso!')
        
        # 2. EDITAR TURMA
        elif acao == 'editar':
            t = get_object_or_404(Turma, id=request.POST.get('id_turma'))
            t.nome = request.POST.get('novo_nome')
            # Permite corrigir o ano se foi cadastrado errado
            novo_ano = request.POST.get('ano_letivo')
            if novo_ano:
                t.ano_letivo = novo_ano
            t.save()
            messages.success(request, 'Turma atualizada!')
        
        # 3. EXCLUIR TURMA
        elif acao == 'excluir':
            t = get_object_or_404(Turma, id=request.POST.get('id_turma'))
            t.delete()
            messages.success(request, 'Turma excluída!')
        
        return redirect('gerenciar_turmas')

    # LISTAGEM
    # Filtra apenas alunos ATIVOS (Cursando) para a contagem não pegar ex-alunos
    turmas = Turma.objects.annotate(
        qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'))
    ).order_by('-ano_letivo', 'nome') # Ordena: Ano mais novo primeiro, depois alfabético
    
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
# 📊 RELATÓRIO DE PROFICIÊNCIA
# ==============================================================================

@login_required
def gerar_relatorio_proficiencia(request):
    import io
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from datetime import datetime

    # 1. Recupera Filtros
    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')  # <--- AGORA VAI RECEBER O ID
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')

    # Configs Gerais
    config = ConfiguracaoSistema.objects.first()
    nome_escola = config.nome_escola if config else "SAMI EDUCACIONAL"
    cor_pri = colors.HexColor(config.cor_primaria) if config else colors.HexColor("#1e293b")
    
    # 2. Filtra Dados
    resultados = Resultado.objects.select_related('avaliacao', 'matricula__aluno', 'matricula__turma')
    filtros_texto = []
    titulo_relatorio = "RELATÓRIO PEDAGÓGICO DE PROFICIÊNCIA"

    if disciplina_id:
        try:
            disc = Disciplina.objects.get(id=disciplina_id)
            resultados = resultados.filter(avaliacao__disciplina=disc)
            filtros_texto.append(f"Disciplina: {disc.nome}")
        except: pass

    if turma_id:
        try:
            turma = Turma.objects.get(id=turma_id)
            resultados = resultados.filter(avaliacao__turma=turma)
            filtros_texto.append(f"Turma: {turma.nome}")
        except: pass

    # LÓGICA DO ALUNO (O pulo do gato)
    if aluno_id:
        try:
            aluno = Aluno.objects.get(id=aluno_id)
            resultados = resultados.filter(matricula__aluno=aluno)
            filtros_texto.append(f"ALUNO: {aluno.nome_completo}")
            titulo_relatorio = "RELATÓRIO INDIVIDUAL DE DESEMPENHO"
        except: pass

    if avaliacao_id:
        try:
            av = Avaliacao.objects.get(id=avaliacao_id)
            resultados = resultados.filter(avaliacao=av)
            filtros_texto.append(f"Prova: {av.titulo}")
        except: pass
    
    if data_inicio: resultados = resultados.filter(avaliacao__data_aplicacao__gte=data_inicio)
    if data_fim: resultados = resultados.filter(avaliacao__data_aplicacao__lte=data_fim)

    if not filtros_texto: filtros_texto.append("Visão Geral da Escola")

    # 3. Processa Dados (Agrega descritores)
    respostas_qs = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related(
        'item_gabarito__descritor', 'questao__descritor'
    )

    stats = {}
    total_itens_respondidos = 0

    for resp in respostas_qs:
        desc = None
        if resp.item_gabarito and resp.item_gabarito.descritor: desc = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor: desc = resp.questao.descritor
        
        if desc:
            cod = desc.codigo
            if cod not in stats: stats[cod] = {'desc': desc.descricao, 'total': 0, 'acertos': 0}
            stats[cod]['total'] += 1
            if resp.acertou: stats[cod]['acertos'] += 1
            total_itens_respondidos += 1

    dados_ordenados = sorted(stats.items())

    # --- 4. GERA O PDF ---
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    styles = getSampleStyleSheet()

    # Cabeçalho
    header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=16, textColor=cor_pri, spaceAfter=2, fontName='Helvetica-Bold')
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, textColor=colors.grey, spaceAfter=12)
    
    elements.append(Paragraph(f"{nome_escola.upper()}", header_style))
    elements.append(Paragraph(titulo_relatorio, sub_style))
    elements.append(Spacer(1, 10))
    
    # Caixa de Contexto
    contexto_texto = " | ".join(filtros_texto)
    data_geracao = datetime.now().strftime('%d/%m/%Y às %H:%M')
    
    # Calcula Média Geral dos resultados filtrados para exibir no topo
    media_filtrada = resultados.aggregate(Avg('percentual'))['percentual__avg'] or 0
    media_formatada = str(round(media_filtrada/10, 1)).replace('.', ',')

    t_ctx = Table([
        [f"CONTEXTO: {contexto_texto}"],
        [f"NOTA MÉDIA NO PERÍODO: {media_formatada} | ITENS ANALISADOS: {total_itens_respondidos}"]
    ], colWidths=[540])
    
    t_ctx.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f1f5f9")),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.black),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('PADDING', (0,0), (-1,-1), 10),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
    ]))
    elements.append(t_ctx)
    elements.append(Spacer(1, 20))

    # Tabela de Dados
    if not dados_ordenados:
        elements.append(Paragraph("Nenhum dado encontrado para os filtros selecionados.", styles['Normal']))
    else:
        # Cabeçalho da Tabela
        data_table = [['CÓDIGO', 'DESCRIÇÃO DA HABILIDADE', 'QTD', '% ACERTO', 'NÍVEL']]

        for cod, d in dados_ordenados:
            perc = (d['acertos'] / d['total']) * 100 if d['total'] > 0 else 0
            
            # Cores de Nível
            cor_nivel = colors.red
            nivel_txt = "CRÍTICO"
            if perc >= 80: 
                cor_nivel = colors.green; nivel_txt = "ADEQUADO"
            elif perc >= 60: 
                cor_nivel = colors.orange; nivel_txt = "INTERMED."
            
            desc_para = Paragraph(d['desc'], ParagraphStyle('d', fontSize=8, leading=9))
            nivel_para = Paragraph(f"<font color='{cor_nivel.hexval()}'><b>{nivel_txt}</b></font>", ParagraphStyle('n', alignment=1))

            row = [
                Paragraph(f"<b>{cod}</b>", styles['Normal']),
                desc_para,
                str(d['total']),
                f"{perc:.1f}%",
                nivel_para
            ]
            data_table.append(row)

        t = Table(data_table, colWidths=[50, 330, 40, 60, 70])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), cor_pri), 
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")), 
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]), 
        ]))
        elements.append(t)
    
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(f"<i>Gerado em {data_geracao}</i>", ParagraphStyle('footer', fontSize=8, textColor=colors.grey, alignment=1)))

    doc.build(elements)
    buffer.seek(0)
    
    filename = "Relatorio_Geral.pdf"
    if aluno_id and resultados.exists():
        filename = f"Relatorio_{resultados.first().matricula.aluno.nome_completo.split()[0]}.pdf"
        
    return FileResponse(buffer, as_attachment=True, filename=filename)

@login_required
def api_filtrar_alunos(request):
    turma_id = request.GET.get('turma_id')
    # CORREÇÃO: Filtra por matrícula ativa na turma
    if turma_id:
        matriculas = Matricula.objects.filter(turma_id=turma_id, status='CURSANDO').select_related('aluno').order_by('aluno__nome_completo')
        data = [{'id': m.aluno.id, 'nome': m.aluno.nome_completo} for m in matriculas]
    else:
        data = []
    return JsonResponse(data, safe=False)


# PERFIL DO ALUNO E DESEMPENHO.

@login_required
def perfil_aluno(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    
    # 1. Histórico de Resultados (Corrigido para buscar via matrícula)
    resultados = Resultado.objects.filter(matricula__aluno=aluno).select_related('avaliacao', 'avaliacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    # 2. Dados para o Gráfico de Evolução
    labels_evo = [res.avaliacao.titulo[:15] + '...' for res in resultados] 
    dados_evo = [float(res.percentual) for res in resultados]
    
    # 3. Média Geral
    media_geral = sum(dados_evo) / len(dados_evo) if dados_evo else 0
    
    # 4. Análise de Habilidades (Pontos Fortes e Fracos)
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related('item_gabarito__descritor', 'questao__descritor')
    stats_descritores = {}
    
    for resp in respostas:
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
    
    habilidades_fortes = sorted(lista_habilidades, key=lambda x: x['perc'], reverse=True)[:5]
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

@login_required
def mapa_calor(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # 1. Pegamos todas as questões (colunas)
    itens = ItemGabarito.objects.filter(avaliacao=avaliacao).select_related('descritor').order_by('numero')
    
    # 2. Pegamos resultados (Corrigido para buscar via matrícula)
    resultados = Resultado.objects.filter(avaliacao=avaliacao).select_related('matricula__aluno').order_by('matricula__aluno__nome_completo')
    
    matriz_dados = []
    
    for res in resultados:
        respostas = RespostaDetalhada.objects.filter(resultado=res)
        mapa_respostas = {r.item_gabarito_id: r.acertou for r in respostas}
        
        linha_questoes = []
        acertos_count = 0
        
        for item in itens:
            status = mapa_respostas.get(item.id) 
            linha_questoes.append({
                'numero': item.numero,
                'acertou': status,
                'descritor': item.descritor.codigo if item.descritor else '-'
            })
            if status: acertos_count += 1
            
        nota_calculada = round(res.percentual / 10, 1) if res.percentual else 0.0

        matriz_dados.append({
            'aluno': res.matricula.aluno, # Pega o aluno da matrícula
            'questoes': linha_questoes,
            'nota': nota_calculada,
            'total_acertos': acertos_count
        })

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


# BOLETIM PDF
def gerar_boletim_pdf(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    # CORREÇÃO: Busca por matrícula
    resultados = Resultado.objects.filter(matricula__aluno=aluno).select_related('avaliacao', 'avaliacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    # Busca a matrícula atual para exibir turma no PDF
    matricula_atual = Matricula.objects.filter(aluno=aluno, status='CURSANDO').last()
    nome_turma = matricula_atual.turma.nome if matricula_atual else "Sem Turma"

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

            if i == len(resultados) - 1: ultima_nota = nota_aluno
            if i == len(resultados) - 2: nota_anterior = nota_aluno
            
        media_geral = round(soma_notas / len(resultados), 1)
    else:
        media_geral = 0.0

    # --- 1.1 PROCESSAMENTO DE HABILIDADES ---
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related('item_gabarito__descritor', 'questao__descritor')
    
    stats_habilidades = {}
    for resp in respostas:
        desc = None
        if resp.item_gabarito and resp.item_gabarito.descritor: desc = resp.item_gabarito.descritor
        elif resp.questao and resp.questao.descritor: desc = resp.questao.descritor
        
        if desc:
            if desc.codigo not in stats_habilidades:
                stats_habilidades[desc.codigo] = {'texto': desc.descricao, 'total': 0, 'acertos': 0}
            stats_habilidades[desc.codigo]['total'] += 1
            if resp.acertou: stats_habilidades[desc.codigo]['acertos'] += 1
            
    pontos_fortes = []
    pontos_atencao = []
    
    for cod, dados in stats_habilidades.items():
        perc = (dados['acertos'] / dados['total']) * 100
        texto_fmt = f"{cod} - {dados['texto'][:35]}..."
        if perc >= 70: pontos_fortes.append(texto_fmt)
        elif perc <= 50: pontos_atencao.append(texto_fmt)
    
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
    # Usa a turma da matrícula encontrada
    c.drawString(120, y_info - 10, f"Matrícula: #{aluno.id}  •  Turma: {nome_turma}")
    
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
    graph_h = 100 
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
        graph_width = 450
        x_start = 65
        
        if len(dados_grafico) == 1:
            dado = dados_grafico[0]
            c.setFillColor(COR_ACCENT)
            h_bar = (dado['aluno'] / 10) * graph_h
            c.roundRect(center_x - 20, y_base, 40, h_bar, 4, fill=1, stroke=0)
            c.setFillColor(COR_DEEP)
            c.drawCentredString(center_x, y_base + h_bar + 5, str(dado['aluno']))
            c.drawCentredString(center_x, y_base - 15, dado['label'])
        else:
            step_x = graph_width / (len(dados_grafico) - 1)
            coords_x = [x_start + (i * step_x) for i in range(len(dados_grafico))]
            
            p = c.beginPath()
            p.moveTo(coords_x[0], y_base)
            for i in range(len(dados_grafico)):
                y_pt = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
                p.lineTo(coords_x[i], y_pt)
            p.lineTo(coords_x[-1], y_base)
            p.close()
            c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.15))
            c.drawPath(p, fill=1, stroke=0)
            
            c.setStrokeColor(COR_ACCENT); c.setLineWidth(2)
            for i in range(len(dados_grafico) - 1):
                y1 = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
                y2 = y_base + (dados_grafico[i+1]['aluno'] / 10 * graph_h)
                c.line(coords_x[i], y1, coords_x[i+1], y2)
                
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
    
    y_current = y_table_title - h_t - 40

    # --- 7. QUADRO DE HABILIDADES (RAIO-X) ---
    if pontos_fortes or pontos_atencao:
        c.setFillColor(COR_DEEP)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y_current, "Raio-X de Habilidades (Pedagógico)")
        y_current -= 20

        data_hab = [['DOMINADAS (+70%)', 'EM DESENVOLVIMENTO (-50%)']]
        
        max_len = max(len(pontos_fortes), len(pontos_atencao))
        if max_len == 0: max_len = 1 
        
        for i in range(max_len):
            forte = pontos_fortes[i] if i < len(pontos_fortes) else ""
            fraco = pontos_atencao[i] if i < len(pontos_atencao) else ""
            data_hab.append([forte, fraco])

        t_hab = Table(data_hab, colWidths=[255, 255])
        t_hab.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (0,0), colors.HexColor("#dcfce7")), 
            ('BACKGROUND', (1,0), (1,0), colors.HexColor("#fee2e2")), 
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
    
    # --- 8. RODAPÉ ---
    y_footer = 50
    
    tendencia = ""
    if len(resultados) >= 2:
        if ultima_nota > nota_anterior: tendencia = " Observa-se uma tendência de evolução positiva."
        elif ultima_nota < nota_anterior: tendencia = " Observa-se uma leve queda recente que requer atenção."

    msg_texto = ""
    if media_geral >= 8: msg_texto = f"Desempenho excelente! O aluno demonstra domínio consistente dos conteúdos.{tendencia}"
    elif media_geral >= 6: msg_texto = f"Desempenho satisfatório. Atende às expectativas, mas pode avançar mais.{tendencia}"
    else: msg_texto = f"Situação de alerta. O aluno encontra-se abaixo da média, sendo fortemente recomendado reforço escolar.{tendencia}"

    c.setFillColor(colors.HexColor("#f8fafc"))
    c.roundRect(40, y_footer, width - 80, 50, 6, fill=1, stroke=0)
    
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y_footer + 32, "PARECER DO SISTEMA:")
    
    styles = getSampleStyleSheet()
    estilo_parecer = ParagraphStyle(
        'ParecerStyle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=COR_TEXT,
        leading=11
    )
    
    parecer_para = Paragraph(msg_texto, estilo_parecer)
    largura_disponivel = width - 160 - 50 
    
    w_p, h_p = parecer_para.wrap(largura_disponivel, 40)
    parecer_para.drawOn(c, 160, y_footer + 38 - h_p)

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
    """
    Gera cartões resposta com QR Code baseado na MATRÍCULA (M).
    Formato QR: A{avaliacao_id}-M{matricula_id}
    """
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # Define quais matrículas vão receber o cartão
    if avaliacao.matricula: 
        # Caso seja prova de recuperação (apenas 1 aluno)
        matriculas = [avaliacao.matricula]
    else:
        # Caso seja prova da turma toda (apenas ativos)
        matriculas = Matricula.objects.filter(
            turma=avaliacao.turma, 
            status='CURSANDO'
        ).select_related('aluno').order_by('aluno__nome_completo')
    
    # Configuração do PDF
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
    
    total_questoes = ItemGabarito.objects.filter(avaliacao=avaliacao).count() or 10
    limite_coluna_1 = 15 
    
    aluno_idx = 0
    total_alunos = len(matriculas)
    
    while aluno_idx < total_alunos:
        for pos_x, pos_y in positions:
            if aluno_idx >= total_alunos: break
            
            mat = matriculas[aluno_idx]
            aluno = mat.aluno
            
            # Desenha Borda Pontilhada (Corte)
            c.setStrokeColor(colors.black)
            c.setLineWidth(1)
            c.setDash([2, 4])
            c.rect(pos_x, pos_y, card_w, card_h, stroke=1, fill=0)
            c.setDash([])

            # Marcadores Fiduciais (Para a IA ler)
            c.setFillColor(colors.black)
            marker_size = 15
            # Top-Left, Top-Right, Bottom-Left, Bottom-Right
            c.rect(pos_x + 10, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + 10, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)

            # --- GERAÇÃO DO QR CODE (ATUALIZADO PARA MATRÍCULA 'M') ---
            qr_data = f"A{avaliacao.id}-M{mat.id}"  # Ex: A15-M203
            
            qr = qrcode.QRCode(box_size=2, border=0)
            qr.add_data(qr_data)
            qr.make(fit=True)
            img_qr = qr.make_image(fill_color="black", back_color="white")
            qr_img_reader = ImageReader(img_qr._img)
            
            c.drawImage(qr_img_reader, pos_x + card_w - 70, pos_y + 20, width=50, height=50)
            # ---------------------------------------------------------
            
            # Dados do Aluno (Texto)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(pos_x + 35, pos_y + card_h - 25, "CARTÃO RESPOSTA")
            
            c.setFont("Helvetica", 9)
            c.drawString(pos_x + 35, pos_y + card_h - 45, f"Aluno: {aluno.nome_completo[:25]}")
            c.drawString(pos_x + 35, pos_y + card_h - 58, f"Prova: {avaliacao.titulo[:25]}")
            
            c.setFont("Helvetica", 8)
            c.drawString(pos_x + 35, pos_y + card_h - 70, f"Turma: {mat.turma.nome} | Matrícula: {mat.id}")
            
            # Bolinhas (Questões)
            y_start = pos_y + card_h - 95
            x_col1 = pos_x + 30
            x_col2 = pos_x + card_w/2 + 10 
            
            c.setFont("Helvetica", 9)
            
            for q_num in range(1, total_questoes + 1):
                if q_num <= limite_coluna_1:
                    curr_x = x_col1
                    curr_y = y_start - ((q_num - 1) * 16)
                else:
                    idx_col2 = q_num - limite_coluna_1 - 1
                    curr_x = x_col2
                    curr_y = y_start - (idx_col2 * 16)
                    if curr_y < (pos_y + 80): 
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

    # Função auxiliar para limpar e converter a nota
    def processar_nota(valor_str):
        if not valor_str or valor_str.strip() == '':
            return None # Retorna None se estiver vazio
        try:
            val = float(valor_str.replace(',', '.'))
            # Garante limites no Backend também
            return max(0.0, min(10.0, val))
        except ValueError:
            return None

    if turma_id:
        turma_selecionada = get_object_or_404(Turma, id=turma_id)
        matriculas = Matricula.objects.filter(turma_id=turma_id, status='CURSANDO').select_related('aluno').order_by('aluno__nome_completo')
        
        if request.method == 'POST':
            salvos = 0
            ignorados = 0
            
            for mat in matriculas:
                # Captura os 5 campos
                raw_freq = request.POST.get(f'freq_{mat.id}')
                raw_atv = request.POST.get(f'atv_{mat.id}')
                raw_comp = request.POST.get(f'comp_{mat.id}')
                raw_pp = request.POST.get(f'pp_{mat.id}')
                raw_pb = request.POST.get(f'pb_{mat.id}')
                
                notas = [
                    processar_nota(raw_freq), processar_nota(raw_atv), 
                    processar_nota(raw_comp), processar_nota(raw_pp), 
                    processar_nota(raw_pb)
                ]

                # REGRA DE OURO: Verifica se TODOS os campos têm valor (não são None)
                if all(n is not None for n in notas):
                    NDI.objects.update_or_create(
                        matricula=mat, bimestre=bimestre,
                        defaults={
                            'nota_frequencia': notas[0],
                            'nota_atividade': notas[1],
                            'nota_comportamento': notas[2],
                            'nota_prova_parcial': notas[3],
                            'nota_prova_bimestral': notas[4]
                        }
                    )
                    salvos += 1
                else:
                    # Se algum campo tiver valor mas não todos, contamos como ignorado/incompleto
                    # (A validação JS deve impedir isso, mas o backend protege)
                    if any(n is not None for n in notas):
                        ignorados += 1

            msg = f"Sucesso! Notas de {salvos} alunos atualizadas."
            if ignorados > 0:
                messages.warning(request, f"{msg} Atenção: {ignorados} alunos tinham dados incompletos e não foram salvos.")
            else:
                messages.success(request, msg)
                
            return redirect(f"{request.path}?turma={turma_id}&bimestre={bimestre}")

        for mat in matriculas:
            ndi = NDI.objects.filter(matricula=mat, bimestre=bimestre).first()
            alunos_data.append({'obj': mat, 'ndi': ndi})

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
    
    # Busca disciplinas
    disciplinas_qs = Disciplina.objects.values_list('nome', flat=True).order_by('nome')
    disciplina_selecionada = request.GET.get('disciplina')
    
    if not disciplina_selecionada:
        if disciplinas_qs.exists():
            disciplina_selecionada = disciplinas_qs.first()
        else:
            disciplina_selecionada = 'Língua Portuguesa'

    turmas = Turma.objects.all().order_by('nome')
    
    plano = None
    dados_kanban = {}
    planos_para_importar = [] # Lista de planos disponíveis para copiar

    for b in range(1, 5):
        dados_kanban[b] = {'TODO': [], 'DOING': [], 'DONE': []}

    if turma_id:
        turma = get_object_or_404(Turma, id=turma_id)
        
        # Cria ou Pega o plano atual
        plano, created = PlanoEnsino.objects.get_or_create(
            turma=turma, 
            disciplina_nome=disciplina_selecionada, 
            defaults={'ano_letivo': 2026}
        )

        # Busca outros planos da MESMA disciplina mas OUTRAS turmas (para importar)
        planos_para_importar = PlanoEnsino.objects.filter(
            disciplina_nome=disciplina_selecionada,
            ano_letivo=2026
        ).exclude(id=plano.id)

        if request.method == 'POST':
            acao = request.POST.get('acao')

            # --- AÇÃO: UPLOAD ARQUIVO ---
            if 'arquivo_plano' in request.FILES:
                plano.arquivo = request.FILES['arquivo_plano']
                plano.save()
                messages.success(request, "Arquivo anexado com sucesso!")
            
            # --- AÇÃO: IMPORTAR PLANO ---
            elif acao == 'importar':
                plano_origem_id = request.POST.get('plano_origem_id')
                if plano_origem_id:
                    plano_origem = PlanoEnsino.objects.get(id=plano_origem_id)
                    # Copia os tópicos
                    for topico in plano_origem.topicos.all():
                        TopicoPlano.objects.create(
                            plano=plano,
                            bimestre=topico.bimestre,
                            conteudo=topico.conteudo,
                            status='TODO', # Começa como A Fazer
                            data_prevista=None # Data reseta pois é nova turma
                        )
                    messages.success(request, f"Tópicos importados da turma {plano_origem.turma.nome}!")

            # --- AÇÃO: CRIAR TÓPICO ---
            elif acao == 'criar':
                conteudo = request.POST.get('conteudo')
                bimestre = int(request.POST.get('bimestre'))
                data_str = request.POST.get('data_prevista') # Nova data
                
                if conteudo:
                    TopicoPlano.objects.create(
                        plano=plano, 
                        bimestre=bimestre, 
                        conteudo=conteudo, 
                        status='TODO',
                        data_prevista=data_str if data_str else None
                    )
                    messages.success(request, "Tópico criado!")

            # --- AÇÃO: EDITAR TÓPICO ---
            elif acao == 'editar':
                topico_id = request.POST.get('topico_id')
                topico = get_object_or_404(TopicoPlano, id=topico_id)
                topico.conteudo = request.POST.get('conteudo')
                
                data_str = request.POST.get('data_prevista') # Nova data
                topico.data_prevista = data_str if data_str else None
                
                topico.save()
                messages.success(request, "Tópico atualizado!")

            # --- AÇÃO: EXCLUIR TÓPICO ---
            elif acao == 'excluir':
                topico_id = request.POST.get('topico_id')
                TopicoPlano.objects.filter(id=topico_id).delete()
                messages.warning(request, "Tópico removido.")
            
            return redirect(f"{request.path}?turma={turma_id}&disciplina={disciplina_selecionada}")

        # Carrega tópicos para o Kanban
        topicos = plano.topicos.all().order_by('data_prevista', 'id') # Ordena por data
        for t in topicos:
            dados_kanban[t.bimestre][t.status].append(t)

    return render(request, 'core/plano_anual.html', {
        'turmas': turmas,
        'disciplinas': disciplinas_qs,
        'turma_selecionada_id': int(turma_id) if turma_id else None,
        'disciplina_atual': disciplina_selecionada,
        'plano': plano,
        'dados_kanban': dados_kanban,
        'planos_para_importar': planos_para_importar # Passa para o template
    })

@login_required
def imprimir_plano_pdf(request, plano_id):
    plano = get_object_or_404(PlanoEnsino, id=plano_id)
    
    # Organiza tópicos
    topicos_por_bimestre = {1: [], 2: [], 3: [], 4: []}
    for t in plano.topicos.all().order_by('bimestre', 'id'):
        topicos_por_bimestre[t.bimestre].append(t)

    html_string = render_to_string('core/relatorios/plano_pdf.html', {
        'plano': plano,
        'topicos_por_bimestre': topicos_por_bimestre,
        'data_geracao': timezone.now()
    })

    result = BytesIO()
    
    # Gera o PDF usando xhtml2pdf
    pdf = pisa.pisaDocument(BytesIO(html_string.encode("UTF-8")), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Plano_{plano.disciplina_nome}.pdf"'
        return response
    
    return HttpResponse("Erro ao gerar PDF", status=500)

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
    # Função legada, mantida por segurança
    return JsonResponse({'status': 'ok'})

@login_required
def api_gerar_questao(request):
    disciplina_id = request.GET.get('disciplina_id')
    topico = request.GET.get('topico')
    dificuldade = request.GET.get('dificuldade')
    
    disciplina = "Geral"
    if disciplina_id:
        disc_obj = Disciplina.objects.filter(id=disciplina_id).first()
        if disc_obj: disciplina = disc_obj.nome

    descritor_cod = request.GET.get('descritor') 
    habilidade_texto = "Foco em competências gerais"
    if descritor_cod:
        desc = Descritor.objects.filter(codigo=descritor_cod).first()
        if desc: habilidade_texto = f"{desc.codigo} - {desc.descricao}"

    dados_ia = gerar_questao_ia(disciplina, topico, habilidade_texto, dificuldade)
    return JsonResponse(dados_ia)

@login_required
def gerenciar_descritores(request):
    filtro_disc = request.GET.get('disciplina')
    filtro_fonte = request.GET.get('fonte')

    disciplinas_queryset = Disciplina.objects.all().order_by('nome')

    if filtro_disc:
        disciplinas_queryset = disciplinas_queryset.filter(id=filtro_disc)

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

    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'excluir':
            desc_id = request.POST.get('descritor_id')
            Descritor.objects.filter(id=desc_id).delete()
            messages.success(request, 'Removido com sucesso.')
        elif acao == 'salvar':
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
        'todas_disciplinas': Disciplina.objects.all().order_by('nome'), 
        'filtro_atual_disc': int(filtro_disc) if filtro_disc else '',
        'filtro_atual_fonte': filtro_fonte or ''
    }
    return render(request, 'core/gerenciar_descritores.html', context)

def upload_correcao_cartao(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    if request.method == 'POST' and request.FILES.get('foto_cartao'):
        foto = request.FILES['foto_cartao']
        # Aqui também precisamos do ID do aluno, mas idealmente lemos do QR Code
        # Se for manual, o professor seleciona. Vamos assumir leitura automática por enquanto.
        
        path = f"media/temp/{foto.name}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb+') as destination:
            for chunk in foto.chunks():
                destination.write(chunk)
        
        # Chama a API interna de leitura
        # (Para simplificar, redireciona a lógica para lá ou duplica aqui com as devidas correções)
        # Por hora, vamos manter simples e não implementar a lógica completa aqui, pois ela está na API
        pass 

    # GET
    # CORREÇÃO: Busca matrículas
    matriculas = Matricula.objects.filter(turma=avaliacao.turma, status='CURSANDO')
    return render(request, 'core/professor/upload_cartao.html', {'avaliacao': avaliacao, 'matriculas': matriculas})

# ==========================================
# 1. API DE LEITURA (COM INTEGRAÇÃO QR CODE)
# ==========================================
@csrf_exempt 
def api_ler_cartao(request):
    """
    Recebe a foto do cartão, lê o QR Code (Axx-Mxx) e as bolinhas.
    Retorna o ID do ALUNO correspondente à matrícula lida.
    """
    if request.method == 'POST' and request.FILES.get('foto'):
        path = ""
        try:
            foto = request.FILES['foto']
            avaliacao_id = request.POST.get('avaliacao_id')
            
            # Salva temporariamente
            path = f"media/temp/{foto.name}"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb+') as destination:
                for chunk in foto.chunks():
                    destination.write(chunk)

            # Define qtd de questões baseada na avaliação (se houver)
            qtd_questoes = 10
            if avaliacao_id:
                qtd = ItemGabarito.objects.filter(avaliacao_id=avaliacao_id).count()
                if qtd > 0: qtd_questoes = qtd

            # Processa OMR (Bolinhas)
            scanner = OMRScanner()
            resultado = scanner.processar_cartao(path, qtd_questoes=qtd_questoes)
            
          # --- LÓGICA DO QR CODE (CORRIGIDA) ---
            if resultado.get('qr_code'):
                try:
                    codigo = resultado['qr_code'] # Ex: "A34-M559"
                    partes = codigo.split('-') 
                    
                    for p in partes:
                        # SUPORTE NOVO: M = Matrícula (O que estamos usando)
                        if p.startswith('M'):
                            matricula_id = int(p[1:])
                            try:
                                mat = Matricula.objects.get(id=matricula_id)
                                # AQUI ESTÁ A CORREÇÃO:
                                # Devolvemos a matrícula ID para o select funcionar
                                resultado['matricula_detected_id'] = mat.id 
                                resultado['aluno_nome'] = mat.aluno.nome_completo
                            except Matricula.DoesNotExist:
                                print(f"Matrícula {matricula_id} não encontrada.")

                        # SUPORTE LEGADO: U = Usuário (Caso antigo)
                        elif p.startswith('U'):
                            aluno_id = int(p[1:])
                            # Tenta achar a matrícula desse aluno na turma da prova (se possível)
                            # Se não, manda só o aluno_id
                            resultado['aluno_detectado_id'] = aluno_id
                            
                except Exception as e:
                    print(f"Erro ao interpretar QR Code '{codigo}': {e}")

            # Limpeza
            if os.path.exists(path):
                os.remove(path)

            return JsonResponse(resultado)

        except Exception as e:
            if os.path.exists(path):
                os.remove(path)
            return JsonResponse({'sucesso': False, 'erro': str(e)})

    return JsonResponse({'sucesso': False, 'erro': 'Nenhuma imagem enviada'})

def central_ajuda(request):
    if request.user.is_authenticated and request.user.is_staff:
        tutoriais = Tutorial.objects.filter(publico__in=['PROF', 'TODOS'])
        publico_alvo = "Professor"
    else:
        tutoriais = Tutorial.objects.filter(publico__in=['ALUNO', 'TODOS'])
        publico_alvo = "Estudante"

    categorias = CategoriaAjuda.objects.all()
    
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
    try:
        # Pega a matrícula ativa do usuário logado
        aluno = request.user.aluno
        # CORREÇÃO: Busca resultados pela MATRÍCULA
        resultados = Resultado.objects.filter(matricula__aluno=aluno).order_by('-avaliacao__data_aplicacao')
    except:
        return redirect('dashboard')

    media_geral = 0
    if resultados.exists():
        notas_validas = [r.percentual for r in resultados if r.percentual is not None]
        if notas_validas:
            media_geral = sum(notas_validas) / len(notas_validas)

    # RAIO-X
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados)
    
    analise_descritores = {}

    for resp in respostas:
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

    lista_habilidades = []
    for cod, dados in analise_descritores.items():
        porcentagem = (dados['acertos'] / dados['total']) * 100
        dados['porcentagem'] = porcentagem
        lista_habilidades.append(dados)

    lista_habilidades.sort(key=lambda x: x['porcentagem'], reverse=True)

    pontos_fortes = [h for h in lista_habilidades if h['porcentagem'] >= 70][:3]
    pontos_atencao = [h for h in lista_habilidades if h['porcentagem'] < 50]
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
    if hasattr(request.user, 'aluno'):
        return dashboard_aluno(request)
    elif request.user.is_staff:
        return dashboard(request) 
    else:
        return HttpResponse("Acesso não autorizado.")
    

def consultar_acesso(request):
    matriculas = None
    termo = request.GET.get('nome_busca') or request.POST.get('nome_busca')
    
    if termo:
        # Busca nas matrículas ATIVAS. 
        # O select_related puxa os dados do Aluno, do Usuário e da Turma em uma tacada só (muito mais rápido)
        matriculas = Matricula.objects.filter(
            aluno__nome_completo__icontains=termo, 
            status='CURSANDO'
        ).select_related('aluno', 'aluno__usuario', 'turma')
    
    return render(request, 'core/consultar_acesso.html', {'matriculas': matriculas, 'termo_busca': termo})

def logout_view(request):
    logout(request) 
    return redirect('login') 


@login_required
def trocar_senha_aluno(request):
    if request.method == 'POST':
        nova_senha = request.POST.get('nova_senha')
        confirmacao = request.POST.get('confirmacao_senha')
        
        if not nova_senha or len(nova_senha) < 6:
            messages.error(request, 'A senha deve ter pelo menos 6 caracteres.')
            return redirect('dashboard_aluno')
            
        if nova_senha != confirmacao:
            messages.error(request, 'As senhas não conferem.')
            return redirect('dashboard_aluno')
            
        u = request.user
        u.set_password(nova_senha)
        u.save()
        
        update_session_auth_hash(request, u)
        
        messages.success(request, 'Senha alterada com sucesso! Não esqueça a nova senha.')
        
    return redirect('dashboard_aluno')

@login_required
def gerar_acessos_em_massa(request):
    """
    Cria usuários automaticamente para alunos que ainda não têm login.
    Login: nome.sobrenome (ex: nicolas.castro)
    Senha: CPF (apenas números) ou 'Mudar123' se não tiver CPF.
    """
    if not request.user.is_superuser:
        messages.error(request, "Apenas administradores podem realizar esta ação.")
        return redirect('dashboard')

    from django.utils.text import slugify
    
    alunos_sem_acesso = Aluno.objects.filter(usuario__isnull=True)
    criados = 0

    for aluno in alunos_sem_acesso:
        try:
            # 1. Gera o Login (slugify remove acentos e espaços)
            partes_nome = slugify(aluno.nome_completo).split('-')
            
            if len(partes_nome) >= 2:
                username_base = f"{partes_nome[0]}.{partes_nome[-1]}" 
            else:
                username_base = partes_nome[0] 
            
            username = username_base
            contador = 1
            from django.contrib.auth.models import User
            while User.objects.filter(username=username).exists():
                username = f"{username_base}{contador}"
                contador += 1

            # 2. Define a Senha (CPF limpo ou Padrão)
            password = "Mudar123" 
            if aluno.cpf:
                senha_cpf = aluno.cpf.replace('.', '').replace('-', '').strip()
                if senha_cpf:
                    password = senha_cpf

            # 3. Cria o Usuário no Django
            user = User.objects.create_user(username=username, password=password)
            
            # 4. Vincula ao Aluno
            aluno.usuario = user
            aluno.save()
            
            criados += 1
            
        except Exception as e:
            print(f"Erro ao gerar user para {aluno.nome_completo}: {e}")

    if criados > 0:
        messages.success(request, f'Sucesso! {criados} logins de alunos foram gerados.')
    else:
        messages.warning(request, 'Todos os alunos já possuem acesso.')
        
    return redirect('dashboard')


# core/views.py (Adicione no final)

@login_required
def relatorio_ndi_print(request, turma_id, bimestre):
    turma = get_object_or_404(Turma, id=turma_id)
    matriculas = Matricula.objects.filter(turma=turma, status='CURSANDO').select_related('aluno').order_by('aluno__nome_completo')
    
    dados = []
    
    for mat in matriculas:
        ndi = NDI.objects.filter(matricula=mat, bimestre=bimestre).first()
        
        # Valores padrão 0.0 se não existir nota
        freq = ndi.nota_frequencia if ndi else 0.0
        atv = ndi.nota_atividade if ndi else 0.0
        comp = ndi.nota_comportamento if ndi else 0.0
        pp = ndi.nota_prova_parcial if ndi else 0.0
        pb = ndi.nota_prova_bimestral if ndi else 0.0
        
        # Cálculos (Mesma lógica do Javascript)
        parcial = (freq + atv + comp) / 3
        final = (parcial + pp + pb) / 3
        
        status = 'APROVADO' if final >= 6 else 'RECUPERAÇÃO' if final >= 4 else 'REPROVADO'
        
        dados.append({
            'aluno': mat.aluno.nome_completo,
            'notas': {
                'freq': freq, 'atv': atv, 'comp': comp,
                'parcial': parcial,
                'pp': pp, 'pb': pb,
                'final': final
            },
            'status': status
        })

    return render(request, 'core/relatorio_ndi_print.html', {
        'turma': turma,
        'bimestre': bimestre,
        'dados': dados,
        'data_geracao': timezone.now()
    })



@login_required
def api_lancar_nota_ajax(request):
    import json
    
    # 1. RECUPERAR DADOS (GET)
    if request.method == 'GET':
        aluno_id = request.GET.get('aluno_id')
        avaliacao_id = request.GET.get('avaliacao_id')
        
        try:
            resultado = Resultado.objects.filter(
                matricula__aluno_id=aluno_id, 
                matricula__turma__avaliacao__id=avaliacao_id,
                avaliacao_id=avaliacao_id
            ).first()
            
            dados = {'respostas': {}, 'nota': 0, 'ausente': False}
            
            if resultado:
                # Se acertos for None, considera 0 para não quebrar a tela
                acertos = resultado.acertos if resultado.acertos is not None else 0
                dados['nota'] = acertos

                tem_respostas = RespostaDetalhada.objects.filter(resultado=resultado).exists()
                if acertos == 0 and not tem_respostas:
                    dados['ausente'] = True
                
                detalhes = RespostaDetalhada.objects.filter(resultado=resultado)
                for r in detalhes:
                    letra = r.resposta_aluno if r.resposta_aluno else ''
                    if not letra and r.acertou:
                        letra = r.item_gabarito.resposta_correta
                    dados['respostas'][r.item_gabarito.numero] = letra

            return JsonResponse({'sucesso': True, 'dados': dados})
        except Exception as e:
            return JsonResponse({'sucesso': False, 'erro': str(e)})

    # 2. SALVAR DADOS (POST)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            aluno_id = data.get('aluno_id')
            avaliacao_id = data.get('avaliacao_id')
            respostas_aluno = data.get('respostas')
            is_ausente = data.get('ausente', False)

            avaliacao = Avaliacao.objects.get(id=avaliacao_id)
            matricula = Matricula.objects.get(aluno_id=aluno_id, turma=avaliacao.turma, status='CURSANDO')
            
            gabarito = ItemGabarito.objects.filter(avaliacao=avaliacao).order_by('numero')
            qtd_questoes = gabarito.count()

            if qtd_questoes == 0:
                 return JsonResponse({'sucesso': False, 'erro': 'Defina o gabarito antes de lançar notas.'})

            # --- AQUI ESTÁ A CORREÇÃO MÁGICA ---
            # O get_or_create primeiro busca. Se não achar, cria.
            # Mas aqui vamos usar uma lógica manual para garantir que NUNCA seja None.
            
            resultado = Resultado.objects.filter(avaliacao=avaliacao, matricula=matricula).first()

            if not resultado:
                # SE NÃO EXISTE, CRIA JÁ COM ZEROS
                # Isso impede que o 'save()' automático do seu model quebre a conta
                resultado = Resultado(
                    avaliacao=avaliacao,
                    matricula=matricula,
                    total_questoes=qtd_questoes,
                    acertos=0,         # Força 0 em vez de None
                    percentual=0.0     # Força 0.0 em vez de None
                )
                resultado.save() # Salva seguro
            else:
                # Se já existe, atualiza o total de questões para garantir
                resultado.total_questoes = qtd_questoes
                # Se estiver None por algum motivo antigo, corrige agora
                if resultado.acertos is None: resultado.acertos = 0
                if resultado.percentual is None: resultado.percentual = 0.0
                resultado.save()

            # Limpa respostas antigas
            RespostaDetalhada.objects.filter(resultado=resultado).delete()

            # LÓGICA DE AUSENTE
            if is_ausente:
                resultado.acertos = 0
                resultado.percentual = 0.0
                resultado.save()
                return JsonResponse({'sucesso': True, 'msg': 'Aluno marcado como ausente.'})

            # LÓGICA DE PRESENTE (Correção)
            acertos = 0
            objs_resposta = []

            for item in gabarito:
                num_str = str(item.numero)
                letra_aluno = respostas_aluno.get(num_str, '').upper()
                
                acertou = False
                if letra_aluno:
                    if letra_aluno == item.resposta_correta:
                        acertou = True
                        acertos += 1
                    
                    objs_resposta.append(RespostaDetalhada(
                        resultado=resultado,
                        item_gabarito=item,
                        questao=item.questao_banco,
                        acertou=acertou,
                        resposta_aluno=letra_aluno 
                    ))
            
            RespostaDetalhada.objects.bulk_create(objs_resposta)
            
            # ATUALIZA A NOTA FINAL
            resultado.acertos = acertos
            resultado.percentual = (acertos / qtd_questoes) * 100
            resultado.save()

            return JsonResponse({'sucesso': True, 'msg': f'Nota salva: {acertos} acertos.'})

        except Exception as e:
            print(f"ERRO CRÍTICO: {e}") # Isso vai mostrar o erro real no seu terminal preto
            return JsonResponse({'sucesso': False, 'erro': f"Erro interno: {str(e)}"})
        

@login_required
def area_professor(request):
    from django.db.models import Count, Q
    
    nome_exibicao = "Professor(a)" # Valor padrão de segurança

    try:
        # Tenta pegar o perfil de professor
        perfil = request.user.professor_perfil
        
        # LÓGICA DE NOME INTELIGENTE:
        # 1. Tenta o Nome Completo do cadastro de Professor
        # 2. Se não tiver, tenta o Primeiro Nome do Usuário de Login
        # 3. Se não tiver, usa o Login (username)
        if perfil.nome_completo:
            nome_exibicao = perfil.nome_completo.split()[0] # Pega só o primeiro nome
        elif request.user.first_name:
            nome_exibicao = request.user.first_name
        else:
            nome_exibicao = request.user.username

        # Filtros (mantendo a correção do alunos_matriculados)
        turmas = perfil.turmas.annotate(
            qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'))
        ).order_by('nome')
        
        provas_recentes = Avaliacao.objects.filter(
            turma__in=perfil.turmas.all(),
            disciplina__in=perfil.disciplinas.all()
        ).order_by('-data_aplicacao')[:5]

    except AttributeError:
        # FALLBACK PARA ADMIN (Se você logar com admin)
        turmas = Turma.objects.annotate(
            qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'))
        ).order_by('nome')
        provas_recentes = Avaliacao.objects.all().order_by('-data_aplicacao')[:5]
        
        # Pega nome do Admin ou usa "Administrador"
        nome_exibicao = request.user.first_name or request.user.username or "Administrador"

    total_alunos = Matricula.objects.filter(status='CURSANDO').count()
    provas_pendentes = provas_recentes.count()

    context = {
        'turmas': turmas,
        'provas_recentes': provas_recentes,
        'kpi_alunos': total_alunos,
        'kpi_pendencias': provas_pendentes,
        'hoje': datetime.now(),
        'nome_professor': nome_exibicao # <--- Agora vai chegar certinho!
    }
    return render(request, 'core/area_professor.html', context)


# Em core/views.py

def login_sucesso_redirect(request):
    """
    Função auxiliar para redirecionar após login.
    Você pode configurar o LOGIN_REDIRECT_URL no settings.py para apontar para cá.
    """
    user = request.user
    # Se for superusuário ou staff -> Painel Gestor
    if user.is_superuser or user.is_staff:
        return redirect('dashboard')
    
    # Se pertencer ao grupo 'Professores' -> Área do Professor
    if user.groups.filter(name='Professores').exists():
        return redirect('area_professor')
        
    # Padrão -> Dashboard Aluno ou outro
    return redirect('dashboard_aluno')

# Em core/views.py

@login_required
def redirecionar_apos_login(request):
    """
    Função 'Semáforo': Decide para onde o usuário vai após logar.
    """
    user = request.user
    
    # 1. Se for Superusuário ou Staff (Diretoria) -> Dashboard Geral
    if user.is_superuser or user.is_staff:
        return redirect('dashboard')
    
    # 2. Se for Professor (tem o perfil vinculado) -> Área do Professor
    if hasattr(user, 'professor_perfil'):
        return redirect('area_professor')

    # 3. Se for Aluno (tem o perfil vinculado) -> Dashboard do Aluno
    # O 'aluno' é o related_name padrão do OneToOneField no model Aluno
    if hasattr(user, 'aluno'):
        return redirect('dashboard_aluno')
        
    # 4. Se não se encaixar em nada, manda para o Dashboard padrão ou Login
    messages.error(request, "Perfil não identificado. Contate a secretaria.")
    return redirect('dashboard')

# Em core/views.py

@login_required
def gerenciar_virada_ano(request):
    """
    Dashboard da Virada: Mostra o cenário de 2025 e permite avançar para 2026.
    """
    # Passo 1: Estatísticas de 2025
    turmas_2025 = Turma.objects.filter(ano_letivo=2025)
    matriculas_2025 = Matricula.objects.filter(turma__in=turmas_2025)
    
    total_alunos_2025 = matriculas_2025.count()
    aprovados = matriculas_2025.filter(situacao='APROVADO').count()
    reprovados = matriculas_2025.filter(situacao='REPROVADO').count()
    # Consideramos 'PENDENTE' quem ainda está como 'CURSANDO'
    pendentes = matriculas_2025.filter(situacao='CURSANDO').count()

    # Passo 2: Verificação se 2026 já existe
    turmas_2026 = Turma.objects.filter(ano_letivo=2026).count()
    
    context = {
        'total_2025': total_alunos_2025,
        'aprovados': aprovados,
        'reprovados': reprovados,
        'pendentes': pendentes,
        'turmas_2026_criadas': turmas_2026 > 0,
        'qtd_turmas_2026': turmas_2026
    }
    return render(request, 'core/virada_ano.html', context)

@login_required
def processar_fechamento_2025(request):
    """
    REGRA DE TRANSIÇÃO (LEGADO 2025):
    Ignora o NDI. Calcula apenas a média das provas (Avaliacoes/Resultados) realizadas.
    """
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # 1. Pega as turmas de 2025
                turmas_2025 = Turma.objects.filter(ano_letivo=2025)
                
                # 2. Pega os alunos que ainda estão cursando
                matriculas = Matricula.objects.filter(turma__in=turmas_2025, status='CURSANDO')
                
                processados = 0
                sem_notas = 0
                
                for mat in matriculas:
                    # BUSCA INTELIGENTE: 
                    # Vai na tabela de Resultados (onde ficam as notas das provas de marcar gabarito/scanner)
                    # Filtra apenas resultados ligados a esta matrícula
                    media_provas = Resultado.objects.filter(matricula=mat).aggregate(Avg('percentual'))['percentual__avg']
                    
                    media_final = 0.0
                    
                    if media_provas is not None:
                        # O percentual vem de 0 a 100 (ex: 80.0). Dividimos por 10 para virar nota (8.0)
                        media_final = float(media_provas) / 10
                    else:
                        # Se o aluno não fez nenhuma prova, marcamos para aviso
                        sem_notas += 1
                        # (Opcional: Você pode decidir se quem não tem nota é Reprovado ou fica Pendente)
                        # Por segurança, vamos considerar 0.
                        media_final = 0.0

                    # Salva a média calculada para histórico
                    mat.media_final = media_final
                    
                    # REGRA DE APROVAÇÃO (Média 6.0)
                    if media_final >= 6.0:
                        mat.status = 'APROVADO'
                        mat.situacao = 'APROVADO'
                    else:
                        mat.status = 'REPROVADO'
                        mat.situacao = 'REPROVADO'
                    
                    mat.save()
                    processados += 1
                    
                msg_aviso = ""
                if sem_notas > 0:
                    msg_aviso = f" (Atenção: {sem_notas} alunos não tinham provas lançadas e ficaram com média 0)."
                    
                messages.success(request, f"Cálculo de 2025 concluído! {processados} alunos processados com base nas provas.{msg_aviso}")
        
        except Exception as e:
            messages.error(request, f"Erro técnico no fechamento: {e}")

        return redirect('gerenciar_virada_ano')

@login_required
def gerar_estrutura_2026(request):
    """
    Clona as turmas de 2025 para 2026 e migra os alunos.
    """
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # 1. Cria as Turmas de 2026 (se não existirem)
                turmas_2025 = Turma.objects.filter(ano_letivo=2025)
                mapa_turmas_promocao = {} # Onde o aprovado vai cair
                mapa_turmas_retencao = {} # Onde o reprovado vai cair
                
                for t_antiga in turmas_2025:
                    nome_base = t_antiga.nome.upper()
                    
                    # Lógica de RETENÇÃO (Reprovado fica na mesma série)
                    t_retencao, _ = Turma.objects.get_or_create(
                        nome=nome_base, 
                        ano_letivo=2026
                    )
                    mapa_turmas_retencao[t_antiga.id] = t_retencao

                    # Lógica de PROMOÇÃO (Aprovado sobe)
                    if "3º" in nome_base or "3" in nome_base:
                        # Formado - não tem turma destino
                        pass
                    else:
                        # Tenta descobrir o próximo nome (1º -> 2º, 2º -> 3º)
                        nome_destino = nome_base
                        if "1º" in nome_base: nome_destino = nome_base.replace("1º", "2º")
                        elif "1" in nome_base: nome_destino = nome_base.replace("1", "2")
                        elif "2º" in nome_base: nome_destino = nome_base.replace("2º", "3º")
                        elif "2" in nome_base: nome_destino = nome_base.replace("2", "3")
                        
                        t_promocao, _ = Turma.objects.get_or_create(
                            nome=nome_destino,
                            ano_letivo=2026
                        )
                        mapa_turmas_promocao[t_antiga.id] = t_promocao
                    
                # 2. Migra os Alunos
                matriculas_2025 = Matricula.objects.filter(turma__ano_letivo=2025)
                migrados = 0
                formados = 0
                
                for mat_antiga in matriculas_2025:
                    # Só migra quem já tem situação definida (não é 'CURSANDO')
                    if mat_antiga.situacao == 'CURSANDO': continue 
                    
                    aluno = mat_antiga.aluno
                    nova_turma = None
                    
                    # REGRA APROVADO
                    if mat_antiga.situacao == 'APROVADO':
                        if "3º" in mat_antiga.turma.nome or "3" in mat_antiga.turma.nome:
                            # Formou! Marca no cadastro do aluno se quiser, mas não cria matrícula 2026
                            formados += 1
                            continue 
                        else:
                            # Vai para a próxima série
                            nova_turma = mapa_turmas_promocao.get(mat_antiga.turma.id)
                            
                    # REGRA REPROVADO
                    elif mat_antiga.situacao == 'REPROVADO':
                        # Fica na mesma série (retenção)
                        nova_turma = mapa_turmas_retencao.get(mat_antiga.turma.id)
                    
                    # CRIA A NOVA MATRÍCULA
                    if nova_turma:
                        # Verifica se já não foi migrado pra não duplicar
                        if not Matricula.objects.filter(aluno=aluno, turma=nova_turma).exists():
                            Matricula.objects.create(
                                aluno=aluno,
                                turma=nova_turma,
                                status='CURSANDO' # Começa 2026 limpo!
                            )
                            migrados += 1

                messages.success(request, f"Sucesso! {migrados} alunos enturmados para 2026. {formados} alunos concluíram o 3º Ano.")
                
        except Exception as e:
            messages.error(request, f"Erro na migração: {e}")
            
        return redirect('gerenciar_virada_ano')