import io
import os
import uuid
import json
import csv
import re
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
from django.contrib.auth.decorators import login_required, user_passes_test
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
    TopicoPlano, ConfiguracaoSistema, Tutorial, CategoriaAjuda, Matricula,
    Professor, Alocacao 
)
from .forms import (
    AvaliacaoForm, ResultadoForm, GerarProvaForm, ImportarQuestoesForm, 
    DefinirGabaritoForm, ImportarAlunosForm, AlunoForm, ProfessorCadastroForm
)

from .services.ai_generator import gerar_questao_ia
from .services.omr_scanner import OMRScanner

def is_staff_check(user):
    return user.is_authenticated and user.is_staff

# ==============================================================================
# 🔥 FUNÇÃO AUXILIAR DE SISTEMA (PARA PROVAS SEM PROFESSOR DEFINIDO)
# ==============================================================================
def get_professor_sistema():
    from django.contrib.auth.models import User
    user_legado, _ = User.objects.get_or_create(username='legado_sistema', defaults={'first_name': 'Sistema'})
    prof_legado, _ = Professor.objects.get_or_create(usuario=user_legado, defaults={'nome_completo': 'Professor Sistema'})
    return prof_legado


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
# 📊 DASHBOARD OTIMIZADO 2.0 
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

    if disciplina_id: resultados = resultados.filter(avaliacao__alocacao__disciplina_id=disciplina_id)
    if serie_id: resultados = resultados.filter(avaliacao__alocacao__turma__nome__startswith=serie_id)
    if turma_id: resultados = resultados.filter(avaliacao__alocacao__turma_id=turma_id)
    if aluno_id: resultados = resultados.filter(matricula__aluno_id=aluno_id)
    if avaliacao_id: resultados = resultados.filter(avaliacao_id=avaliacao_id)
    if data_inicio: resultados = resultados.filter(avaliacao__data_aplicacao__gte=data_inicio)
    if data_fim: resultados = resultados.filter(avaliacao__data_aplicacao__lte=data_fim)

    # --- 2. PROCESSAMENTO OTIMIZADO ---

    # A. KPI & PIZZA
    kpis = resultados.aggregate(
        total=Count('id'),
        media=Avg('percentual'),
        adequado=Count('id', filter=Q(percentual__gte=75)),
        intermediario=Count('id', filter=Q(percentual__gte=50, percentual__lt=75)),
        critico=Count('id', filter=Q(percentual__gte=25, percentual__lt=50)),
        muito_critico=Count('id', filter=Q(percentual__lt=25))
    )

    count_avaliados = kpis['total']
    media_geral = round((kpis['media'] or 0) / 10, 1)
    
    dados_pizza = [kpis['adequado'], kpis['intermediario'], kpis['critico'], kpis['muito_critico']]

    nivel_predominante = "-"
    if count_avaliados > 0:
        idx_max = dados_pizza.index(max(dados_pizza))
        nomes = ["Adequado 🔵", "Intermediário 🟢", "Crítico 🟡", "Muito Crítico 🔴"]
        nivel_predominante = nomes[idx_max]

    qtd_provas = resultados.values('avaliacao').distinct().count()

    detalhes_qs = resultados.select_related('matricula__aluno', 'matricula__turma').only(
        'percentual', 'matricula__aluno__nome_completo', 'matricula__turma__nome'
    )[:500]

    detalhes_pizza = {'Adequado': [], 'Intermediário': [], 'Crítico': [], 'Muito Crítico': []}
    for res in detalhes_qs:
        p = float(res.percentual or 0)
        info = {'nome': res.matricula.aluno.nome_completo, 'turma': res.matricula.turma.nome, 'nota': round(p/10, 1)}
        
        if p >= 75: detalhes_pizza['Adequado'].append(info)
        elif p >= 50: detalhes_pizza['Intermediário'].append(info)
        elif p >= 25: detalhes_pizza['Crítico'].append(info)
        else: detalhes_pizza['Muito Crítico'].append(info)
    
    detalhes_pizza_json = json.dumps(detalhes_pizza)

    # B. PROFICIÊNCIA POR DESCRITOR
    respostas_base = RespostaDetalhada.objects.filter(resultado__in=resultados)
    
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

    # C. RANKING DE QUESTÕES
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

    # E. HEATMAP
    itens_heatmap = []
    matriz_calor = []
    
    if avaliacao_id:
        try:
            av = Avaliacao.objects.get(id=avaliacao_id)
            itens_heatmap = ItemGabarito.objects.filter(avaliacao=av).select_related('descritor').order_by('numero')
            res_heat = resultados.select_related('matricula__aluno').order_by('matricula__aluno__nome_completo')
            
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
    
    filtros = Q(acertou=False) 
    
    if descritor_cod: 
        filtros &= (Q(item_gabarito__descritor__codigo=descritor_cod) | Q(questao__descritor__codigo=descritor_cod))
    
    if request.GET.get('avaliacao'): filtros &= Q(resultado__avaliacao_id=request.GET.get('avaliacao'))
    if request.GET.get('turma'): filtros &= Q(resultado__avaliacao__alocacao__turma_id=request.GET.get('turma'))
    if request.GET.get('serie'): filtros &= Q(resultado__avaliacao__alocacao__turma__nome__startswith=request.GET.get('serie'))
    if request.GET.get('disciplina'): filtros &= Q(resultado__avaliacao__alocacao__disciplina_id=request.GET.get('disciplina'))

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
    if request.GET.get('baixar_modelo'):
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename=modelo_importacao_sami.xlsx'
        df_modelo = pd.DataFrame({
            'NOME COMPLETO': ['Nicolas Castro', 'Ana Souza'],
            'TURMA': ['3º Ano B', '1º Ano A']
        })
        df_modelo.to_excel(response, index=False)
        return response

    if request.method == 'POST':
        form = ImportarAlunosForm(request.POST, request.FILES)
        if form.is_valid():
            erros_log = []
            try:
                arquivo = request.FILES['arquivo_excel']
                
                try:
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
                except Exception as erro_leitura:
                    messages.error(request, f"Erro ao ler o arquivo. Verifique se não está corrompido: {str(erro_leitura)}")
                    return redirect('importar_alunos')
                
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
                        
                        turma_obj, _ = Turma.objects.get_or_create(
                            nome=str(row.get(c_turma, 'SEM TURMA')).strip().upper(),
                            defaults={'ano_letivo': timezone.now().year}
                        )
                        
                        aluno_obj, created_aluno = Aluno.objects.get_or_create(
                            nome_completo=str(raw_nome).strip().upper()
                        )
                        
                        Matricula.objects.get_or_create(
                            aluno=aluno_obj, turma=turma_obj, defaults={'status': 'CURSANDO'}
                        )
                        
                        if created_aluno: criados += 1

                    except Exception as e:
                        erros_log.append(f"Linha {index}: {str(e)}")

                if criados > 0:
                    messages.success(request, f'✅ Sucesso! {criados} novos alunos importados.')
                elif erros_log:
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
# 📝 GESTÃO DE AVALIAÇÕES E PROVAS (COM CADEADO DE SEGURANÇA 🔥)
# ==============================================================================

@login_required
def gerenciar_avaliacoes(request):
    if request.method == 'POST' and 'delete_id' in request.POST:
        av = get_object_or_404(Avaliacao, id=request.POST.get('delete_id'))
        av.delete()
        messages.success(request, 'Avaliação removida com sucesso!')
        return redirect('gerenciar_avaliacoes')

    turma_id = request.GET.get('turma')
    disciplina_id = request.GET.get('disciplina')
    data_filtro = request.GET.get('data')

    # Base da busca 
    avaliacoes = Avaliacao.objects.select_related('alocacao__turma', 'alocacao__disciplina').order_by('-data_aplicacao')
    
    # Listas para os filtros (Botões Select)
    ano_atual = timezone.now().year
    turmas_dropdown = Turma.objects.all().order_by('nome')
    turmas_ativas = Turma.objects.filter(ano_letivo=ano_atual).order_by('nome')
    disciplinas_dropdown = Disciplina.objects.all().order_by('nome')

    # 🔥 A MÁGICA DA SEGURANÇA (O CADEADO) 🔥
    if hasattr(request.user, 'professor_perfil'):
        perfil = request.user.professor_perfil
        
        # 1. Filtra a tabela para ele ver SÓ as provas dele
        avaliacoes = avaliacoes.filter(alocacao__professor=perfil)
        
        # 2. Filtra os botões Select para ele não poder pesquisar turmas de outros
        turmas_dropdown = turmas_dropdown.filter(alocacoes__professor=perfil).distinct()
        turmas_ativas = turmas_ativas.filter(alocacoes__professor=perfil).distinct()
        disciplinas_dropdown = disciplinas_dropdown.filter(alocacoes__professor=perfil).distinct()

    # Aplica os filtros escolhidos pelo usuário na tela
    if turma_id:
        avaliacoes = avaliacoes.filter(alocacao__turma_id=turma_id)
    if disciplina_id:
        avaliacoes = avaliacoes.filter(alocacao__disciplina_id=disciplina_id)
    if data_filtro:
        avaliacoes = avaliacoes.filter(data_aplicacao=data_filtro)

    context = {
        'avaliacoes': avaliacoes,
        'turmas': turmas_dropdown,
        'turmas_ativas': turmas_ativas,
        'disciplinas': disciplinas_dropdown,
        'total_avaliacoes': avaliacoes.count(),
        'filtro_turma': int(turma_id) if turma_id else None,
        'filtro_disciplina': int(disciplina_id) if disciplina_id else None,
        'filtro_data': data_filtro
    }
    
    return render(request, 'core/avaliacoes.html', context)


@login_required
def criar_avaliacao(request):
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        disciplina_id = request.POST.get('disciplina')
        data_aplicacao = request.POST.get('data_aplicacao')
        
        tipo_foco = request.POST.get('tipo_foco')
        turma_id = request.POST.get('turma')
        serie_alvo = request.POST.get('serie_alvo')
        
        acao = request.POST.get('acao')
        modo = request.POST.get('modo_prova')

        if titulo and disciplina_id and data_aplicacao:
            try:
                turmas_alvo = []
                ano_atual = timezone.now().year 
                
                if tipo_foco == 'escola':
                    turmas_alvo = Turma.objects.filter(ano_letivo=ano_atual)
                elif tipo_foco == 'serie':
                    turmas_alvo = Turma.objects.filter(nome__startswith=serie_alvo, ano_letivo=ano_atual)
                else:
                    if turma_id:
                        turmas_alvo = [get_object_or_404(Turma, id=turma_id)]
                
                if not turmas_alvo:
                    messages.error(request, "Nenhuma turma selecionada.")
                    return redirect('criar_avaliacao')

                count = 0
                ultimo_id = None
                
                # 🔥 SEGURANÇA: Garante que a prova saia no nome do Professor Logado
                if hasattr(request.user, 'professor_perfil'):
                    dono_prova = request.user.professor_perfil
                else:
                    dono_prova = get_professor_sistema()
                
                with transaction.atomic():
                    for turma in turmas_alvo:
                        # 🔥 SOLUÇÃO BLINDADA CONTRA DUPLICATAS NO BANCO 🔥
                        aloc = Alocacao.objects.filter(
                            turma=turma,
                            disciplina_id=disciplina_id,
                            professor=dono_prova
                        ).first()
                        
                        if not aloc:
                            aloc = Alocacao.objects.create(
                                turma=turma,
                                disciplina_id=disciplina_id,
                                professor=dono_prova
                            )
                        
                        av = Avaliacao.objects.create(
                            titulo=titulo, 
                            alocacao=aloc, 
                            data_aplicacao=data_aplicacao
                        )
                        ultimo_id = av.id
                        count += 1

                messages.success(request, f'Sucesso! {count} avaliações criadas.')

                if acao == 'salvar_configurar':
                    if modo == 'banco': 
                        return redirect('montar_prova', ultimo_id) 
                    else: 
                        return redirect('definir_gabarito', ultimo_id)
                
                return redirect('gerenciar_avaliacoes')

            except Exception as e:
                messages.error(request, f"Erro ao criar: {e}")
        else:
            messages.error(request, 'Erro: Preencha título, disciplina e data.')

    # Listas para o GET (Quando o usuário entra na tela)
    turmas_qs = Turma.objects.filter(ano_letivo=timezone.now().year).order_by('nome')
    disciplinas_qs = Disciplina.objects.all().order_by('nome')
    
    # 🔥 Filtra os Dropdowns de criar prova se for professor
    if hasattr(request.user, 'professor_perfil'):
        perfil = request.user.professor_perfil
        turmas_qs = turmas_qs.filter(alocacoes__professor=perfil).distinct()
        disciplinas_qs = disciplinas_qs.filter(alocacoes__professor=perfil).distinct()

    context = {
        'turmas': turmas_qs,
        'disciplinas': disciplinas_qs
    }
    return render(request, 'core/criar_avaliacao.html', context)

@login_required
def gerar_prova_pdf(request):
    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        disciplina_id = request.POST.get('disciplina')
        tipo_foco = request.POST.get('tipo_foco') 
        
        aluno_id = request.POST.get('aluno_id')
        turma_id = request.POST.get('turma_id')
        serie_alvo = request.POST.get('serie_alvo')
        
        qtd_questoes = int(request.POST.get('qtd_questoes', 10))
        salvar_sistema = request.POST.get('salvar_sistema') == 'on'

        disciplina_obj = get_object_or_404(Disciplina, id=disciplina_id)
        
        turmas_alvo = []
        matricula_alvo = None
        filtro_erros = Q()

        if tipo_foco == 'aluno' and aluno_id:
            aluno_obj = Aluno.objects.get(id=aluno_id)
            matricula_alvo = Matricula.objects.filter(aluno=aluno_obj, status='CURSANDO').last()
            
            if matricula_alvo:
                turmas_alvo = [matricula_alvo.turma]
                filtro_erros = Q(resultado__matricula=matricula_alvo)
            else:
                messages.error(request, "Aluno sem matrícula ativa.")
                return redirect('gerenciar_avaliacoes')

        elif tipo_foco == 'turma' and turma_id:
            t_obj = get_object_or_404(Turma, id=turma_id)
            turmas_alvo = [t_obj]
            filtro_erros = Q(resultado__matricula__turma=t_obj)
            ano_atual = timezone.now().year

        elif tipo_foco == 'serie' and serie_alvo:
            ano_atual = timezone.now().year
            turmas_alvo = Turma.objects.filter(nome__startswith=serie_alvo, ano_letivo=ano_atual)
            filtro_erros = Q(resultado__matricula__turma__nome__startswith=serie_alvo, resultado__matricula__turma__ano_letivo=ano_atual)

        elif tipo_foco == 'escola':
            ano_atual = timezone.now().year
            turmas_alvo = Turma.objects.filter(ano_letivo=ano_atual)
            filtro_erros = Q(resultado__matricula__turma__ano_letivo=ano_atual)

        if not turmas_alvo:
            messages.error(request, "Nenhuma turma encontrada.")
            return redirect('gerenciar_avaliacoes')

        erros_query = RespostaDetalhada.objects.filter(
            acertou=False, 
            questao__disciplina=disciplina_obj
        ).filter(filtro_erros)

        descritores_criticos = erros_query.values('questao__descritor').annotate(total_erros=Count('id')).order_by('-total_erros')[:5]
        ids_descritores = [item['questao__descritor'] for item in descritores_criticos if item['questao__descritor']]

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
        
        if not questoes_finais:
            messages.error(request, "Não há questões suficientes no banco para esta disciplina.")
            return redirect('gerenciar_avaliacoes')

        if salvar_sistema:
            try:
                # 🔥 SEGURANÇA: Garante que a prova saia no nome do Professor Logado
                if hasattr(request.user, 'professor_perfil'):
                    dono_prova = request.user.professor_perfil
                else:
                    dono_prova = get_professor_sistema()
                
                with transaction.atomic():
                    count = 0
                    for turma in turmas_alvo:
                        titulo_final = f"RECUPERAÇÃO: {titulo}" if matricula_alvo else titulo
                        
                        aloc, _ = Alocacao.objects.get_or_create(
                            turma=turma, disciplina=disciplina_obj, defaults={'professor': dono_prova}
                        )

                        nova_av = Avaliacao.objects.create(
                            titulo=titulo_final,
                            alocacao=aloc,
                            matricula=matricula_alvo, 
                            data_aplicacao=datetime.now().date()
                        )
                        nova_av.questoes.set(questoes_finais)
                        
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

        if len(turmas_alvo) > 1:
            return redirect('gerenciar_avaliacoes')
        
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        
        nome_aluno_pdf = matricula_alvo.aluno.nome_completo if matricula_alvo else "___________________________________"
        
        desenhar_cabecalho_prova(p, titulo, turmas_alvo[0].nome, disciplina_obj.nome)
        
        if matricula_alvo:
            p.setFont("Helvetica-Bold", 10)
            p.setFillColor(colors.black)
            p.drawString(95, 775, nome_aluno_pdf)

        y = 730
        for i, q in enumerate(questoes_finais, 1):
            p.setFont("Helvetica-Bold", 11)
            p.setFillColor(colors.black)
            texto_completo = f"{i}. {q.enunciado}"
            linhas_enunciado = simpleSplit(texto_completo, "Helvetica-Bold", 11, 480)
            
            espaco = (len(linhas_enunciado) * 15) + 120
            if q.imagem: espaco += 150
            espaco += 20 

            if y - espaco < 50:
                p.showPage()
                desenhar_cabecalho_prova(p, titulo, turmas_alvo[0].nome, disciplina_obj.nome)
                if matricula_alvo: p.drawString(95, 775, nome_aluno_pdf)
                y = 730
            
            for linha in linhas_enunciado:
                p.drawString(40, y, linha)
                y -= 15

            if q.imagem:
                try:
                    img_reader = ImageReader(q.imagem.path)
                    iw, ih = img_reader.getSize()
                    aspect = ih / float(iw)
                    h_img = 200 * aspect
                    p.drawImage(img_reader, 50, y - h_img, width=200, height=h_img)
                    y -= (h_img + 10)
                except: pass

            p.setFont("Helvetica", 10)
            opts = [('A', q.alternativa_a), ('B', q.alternativa_b), ('C', q.alternativa_c), ('D', q.alternativa_d)]
            if q.alternativa_e: opts.append(('E', q.alternativa_e))
            
            for l, txt in opts:
                if txt:
                    p.drawString(50, y, f"{l}) {txt}")
                    y -= 15
            
            if q.descritor:
                p.setFont("Helvetica-Oblique", 8) 
                p.setFillColorRGB(0.4, 0.4, 0.4) 
                
                txt_desc = f"Habilidade: {q.descritor.codigo} - {q.descritor.descricao[:90]}"
                if len(q.descritor.descricao) > 90: txt_desc += "..."
                p.drawString(50, y, txt_desc)
                p.setFillColorRGB(0, 0, 0) 
                y -= 15 
            
            y -= 20 

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
    
    desenhar_cabecalho_prova(p, avaliacao.titulo, avaliacao.alocacao.turma.nome, avaliacao.alocacao.disciplina.nome)
    
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

    questoes = Questao.objects.filter(disciplina=avaliacao.alocacao.disciplina).order_by('-id')
    
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

    descritores = Descritor.objects.filter(disciplina=avaliacao.alocacao.disciplina).order_by('codigo')

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
    
    if not itens_salvos.exists() and avaliacao.questoes.exists():
        for i, q in enumerate(avaliacao.questoes.all(), 1):
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
            desc_padrao = Descritor.objects.filter(disciplina=avaliacao.alocacao.disciplina).first()
            
            for i in range(1, qtd + 1):
                ItemGabarito.objects.create(
                    avaliacao=avaliacao, numero=i, resposta_correta='A', descritor=desc_padrao
                )
            return redirect('definir_gabarito', avaliacao_id=avaliacao.id)
        
        else:
            try:
                with transaction.atomic():
                    for item in itens_salvos:
                        nova_resposta = request.POST.get(f'resposta_{item.id}')
                        novo_descritor_id = request.POST.get(f'descritor_{item.id}')
                        
                        if nova_resposta: item.resposta_correta = nova_resposta
                        if novo_descritor_id: item.descritor_id = novo_descritor_id
                        item.save()

                    if request.POST.get('replicar_para_todos') == 'on':
                        provas_irmas = Avaliacao.objects.filter(
                            titulo=avaliacao.titulo, 
                            alocacao__disciplina=avaliacao.alocacao.disciplina,
                            data_aplicacao__year=avaliacao.data_aplicacao.year 
                        ).exclude(id=avaliacao.id)

                        count_replicas = 0
                        
                        for irma in provas_irmas:
                            ItemGabarito.objects.filter(avaliacao=irma).delete()
                            
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

    descritores = Descritor.objects.filter(disciplina=avaliacao.alocacao.disciplina).order_by('codigo')

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
        
        if avaliacao_obj.matricula:
            matriculas_turma = Matricula.objects.filter(id=avaliacao_obj.matricula.id)
        else:
            matriculas_turma = Matricula.objects.filter(
                turma=avaliacao_obj.alocacao.turma, 
                status='CURSANDO'
            ).select_related('aluno').order_by('aluno__nome_completo')

    if request.method == 'POST' and avaliacao_obj:
        matricula_id = request.POST.get('aluno') 
        
        if not matricula_id:
            messages.error(request, "Selecione um aluno.")
            return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

        matricula_obj = get_object_or_404(Matricula, id=matricula_id)
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

        RespostaDetalhada.objects.filter(resultado=resultado).delete()
        
        acertos_contagem = 0
        objs_resposta = []
        
        for item in itens:
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

        if request.POST.get('ausente') == 'true':
            resultado.acertos = 0
            resultado.percentual = 0.0
        else:
            resultado.acertos = acertos_contagem
            qtd = resultado.total_questoes if resultado.total_questoes > 0 else 1
            resultado.percentual = (acertos_contagem / qtd) * 100
        
        resultado.save()
        
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
             from django.http import JsonResponse
             return JsonResponse({'sucesso': True, 'msg': f'Nota salva: {acertos_contagem}'})

        messages.success(request, f'Nota salva: {acertos_contagem}')
        return redirect(f'/lancar_nota/?avaliacao_id={avaliacao_id}')

    return render(request, 'core/lancar_nota.html', {
        'avaliacao_selecionada': avaliacao_obj,
        'itens': itens, 
        'matriculas': matriculas_turma, 
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-data_aplicacao')
    })

# ==============================================================================
# 📋 GERENCIAMENTO GERAL
# ==============================================================================

@login_required
def gerenciar_alunos(request):
    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        if acao == 'criar':
            nome = request.POST.get('nome')
            turma_id = request.POST.get('turma')
            
            is_pcd = request.POST.get('is_pcd') == 'on'
            tipo_deficiencia = request.POST.get('tipo_deficiencia')
            cor_raca = request.POST.get('cor_raca')
            
            if nome and turma_id:
                try:
                    with transaction.atomic():
                        novo_aluno = Aluno.objects.create(
                            nome_completo=nome.upper(),
                            is_pcd=is_pcd,
                            tipo_deficiencia=tipo_deficiencia,
                            cor_raca=cor_raca
                        )
                        turma_obj = Turma.objects.get(id=turma_id)
                        Matricula.objects.create(aluno=novo_aluno, turma=turma_obj, status='CURSANDO')
                        msg_extra = " (Marcado como Inclusão)" if is_pcd else ""
                        messages.success(request, f'Aluno matriculado com sucesso!{msg_extra}')
                except Exception as e:
                    messages.error(request, f'Erro ao cadastrar: {e}')
            else:
                messages.error(request, 'Preencha nome e turma.')

        elif acao == 'editar':
            matricula_id = request.POST.get('matricula_id')
            try:
                mat = get_object_or_404(Matricula, id=matricula_id)
                novo_nome = request.POST.get('nome')
                if novo_nome: mat.aluno.nome_completo = novo_nome.upper()
                
                mat.aluno.is_pcd = request.POST.get('is_pcd') == 'on'
                mat.aluno.tipo_deficiencia = request.POST.get('tipo_deficiencia')
                mat.aluno.cor_raca = request.POST.get('cor_raca')
                mat.aluno.genero = request.POST.get('genero')
                mat.aluno.renda_familiar = request.POST.get('renda_familiar')
                mat.aluno.save() 
                
                nova_turma_id = request.POST.get('turma')
                if nova_turma_id and nova_turma_id != str(mat.turma.id):
                    mat.turma = Turma.objects.get(id=nova_turma_id)
                
                status_novo = request.POST.get('status')
                if status_novo: mat.status = status_novo
                    
                mat.save()
                messages.success(request, 'Dados do aluno atualizados com sucesso!')
            except Exception as e:
                messages.error(request, f'Erro ao editar: {e}')

        elif acao == 'excluir':
            matricula_id = request.POST.get('matricula_id')
            try:
                mat = get_object_or_404(Matricula, id=matricula_id)
                aluno = mat.aluno
                mat.delete() 
                aluno.delete() 
                messages.warning(request, 'Aluno e matrícula removidos.')
            except:
                messages.error(request, 'Erro ao excluir.')

        return redirect('gerenciar_alunos')

    busca = request.GET.get('busca')
    filtro_turma = request.GET.get('turma')
    filtro_serie = request.GET.get('serie')
    apenas_pcd = request.GET.get('apenas_pcd')
    ordem = request.GET.get('ordem', 'nome')
    filtro_ano = request.GET.get('ano', str(timezone.now().year)) 

    matriculas = Matricula.objects.filter(turma__ano_letivo=filtro_ano).select_related('aluno', 'turma')
    
    if str(filtro_ano) == str(timezone.now().year):
        matriculas = matriculas.filter(status='CURSANDO')

    matriculas = matriculas.annotate(media_geral=Avg('resultados__percentual'))

    if busca:
        matriculas = matriculas.filter(
            Q(aluno__nome_completo__icontains=busca) | 
            Q(aluno__cpf__icontains=busca)
        )
    
    if filtro_turma:
        matriculas = matriculas.filter(turma_id=filtro_turma)
        
    if filtro_serie:
        matriculas = matriculas.filter(turma__nome__startswith=filtro_serie)

    if apenas_pcd == 'on':
        matriculas = matriculas.filter(aluno__is_pcd=True)

    if ordem == 'nome':
        matriculas = matriculas.order_by('aluno__nome_completo')
    elif ordem == 'melhores':
        matriculas = matriculas.order_by('-media_geral')
    elif ordem == 'criticos':
        matriculas = matriculas.order_by('media_geral')

    paginator = Paginator(matriculas, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    turmas_para_select = Turma.objects.filter(ano_letivo=filtro_ano).order_by('nome')

    return render(request, 'core/gerenciar_alunos.html', {
        'matriculas': page_obj,
        'turmas': turmas_para_select,
        'busca_atual': busca,
        'turma_selecionada': int(filtro_turma) if filtro_turma else None,
        'serie_selecionada': filtro_serie,
        'apenas_pcd': apenas_pcd,
        'ordem_atual': ordem,
        'ano_atual': filtro_ano 
    })

@login_required
def gerenciar_turmas(request):
    if request.method == 'POST':
        acao = request.POST.get('acao')
        
        if acao == 'criar':
            nome = request.POST.get('nome_turma')
            ano = request.POST.get('ano_letivo', timezone.now().year) 
            
            if nome:
                Turma.objects.create(nome=nome, ano_letivo=ano)
                messages.success(request, 'Turma criada com sucesso!')
        
        elif acao == 'editar':
            t = get_object_or_404(Turma, id=request.POST.get('id_turma'))
            t.nome = request.POST.get('novo_nome')
            novo_ano = request.POST.get('ano_letivo')
            if novo_ano:
                t.ano_letivo = novo_ano
            t.save()
            messages.success(request, 'Turma atualizada!')
        
        elif acao == 'excluir':
            t = get_object_or_404(Turma, id=request.POST.get('id_turma'))
            t.delete()
            messages.success(request, 'Turma excluída!')
        
        return redirect('gerenciar_turmas')

    turmas = Turma.objects.annotate(
        qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'))
    ).order_by('-ano_letivo', 'nome') 
    
    return render(request, 'core/turmas.html', {'turmas': turmas})


@login_required
def listar_questoes(request):
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
                if questao_id:
                    q = Questao.objects.get(id=questao_id)
                    for key, value in dados.items(): setattr(q, key, value)
                    q.save()
                    messages.success(request, 'Questão atualizada!')
                else: 
                    Questao.objects.create(**dados)
                    messages.success(request, 'Nova questão criada!')
            except Exception as e:
                messages.error(request, f'Erro ao salvar: {str(e)}')
                
        return redirect('listar_questoes')

    questoes = Questao.objects.select_related('disciplina', 'descritor').order_by('-id')
    
    filtro_disc = request.GET.get('disciplina')
    filtro_busca = request.GET.get('busca')
    filtro_dificuldade = request.GET.get('dificuldade')
    filtro_serie = request.GET.get('serie')             
    
    if filtro_disc and filtro_disc not in ['None', '']: 
        try:
            questoes = questoes.filter(disciplina_id=int(filtro_disc))
        except ValueError:
            pass 
            
    if filtro_busca and filtro_busca not in ['None', '']:
        questoes = questoes.filter(enunciado__icontains=filtro_busca)

    if filtro_dificuldade and filtro_dificuldade in ['F', 'M', 'D']:
        questoes = questoes.filter(dificuldade=filtro_dificuldade)

    if filtro_serie and filtro_serie in ['1', '2', '3']:
        questoes = questoes.filter(serie=filtro_serie)
    
    paginator = Paginator(questoes, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    context = {
        'page_obj': page_obj,
        'disciplinas': Disciplina.objects.all(),
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
        avaliacao.data_aplicacao = request.POST.get('data_aplicacao')
        
        t_id = request.POST.get('turma')
        d_id = request.POST.get('disciplina')
        prof_sis = avaliacao.alocacao.professor
        
        nova_aloc, _ = Alocacao.objects.get_or_create(
            turma_id=t_id, disciplina_id=d_id, defaults={'professor': prof_sis}
        )
        avaliacao.alocacao = nova_aloc
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
    import os
    from django.conf import settings
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.platypus import Image as RLImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from datetime import datetime
    
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')  
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')

    config = ConfiguracaoSistema.objects.first()
    nome_escola = config.nome_escola if config else "SAMI EDUCACIONAL"
    cor_pri = colors.HexColor(config.cor_primaria) if config else colors.HexColor("#0A2619")
    
    resultados = Resultado.objects.select_related('avaliacao', 'matricula__aluno', 'matricula__turma')
    filtros_texto = []
    titulo_relatorio = "RELATÓRIO PEDAGÓGICO DE PROFICIÊNCIA"

    if disciplina_id:
        try:
            disc = Disciplina.objects.get(id=disciplina_id)
            resultados = resultados.filter(avaliacao__alocacao__disciplina=disc)
            filtros_texto.append(f"Disciplina: {disc.nome}")
        except: pass

    if turma_id:
        try:
            turma = Turma.objects.get(id=turma_id)
            resultados = resultados.filter(avaliacao__alocacao__turma=turma)
            filtros_texto.append(f"Turma: {turma.nome}")
        except: pass

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

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    styles = getSampleStyleSheet()

    header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=16, textColor=cor_pri, spaceAfter=2, fontName='Helvetica-Bold')
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, textColor=colors.grey, spaceAfter=0)
    
    logo_img = None
    if config and config.logo:
        try:
            caminho_imagem = os.path.join(settings.MEDIA_ROOT, str(config.logo))
            if os.path.exists(caminho_imagem):
                logo_img = RLImage(caminho_imagem, width=50, height=50)
            else:
                print(f"Alerta: Arquivo de logo não encontrado no caminho físico: {caminho_imagem}")
        except Exception as e:
            print(f"Erro ao tentar ler o logo para o PDF: {e}")
            pass 

    if logo_img:
        tbl_header = Table([
            [logo_img, Paragraph(f"{nome_escola.upper()}", header_style)],
            ['', Paragraph(titulo_relatorio, sub_style)]
        ], colWidths=[60, 480])
        tbl_header.setStyle(TableStyle([
            ('SPAN', (0,0), (0,1)), 
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))
        elements.append(tbl_header)
    else:
        elements.append(Paragraph(f"{nome_escola.upper()}", header_style))
        elements.append(Paragraph(titulo_relatorio, sub_style))
        
    elements.append(Spacer(1, 15))
    
    contexto_texto = " | ".join(filtros_texto)
    data_geracao = datetime.now().strftime('%d/%m/%Y às %H:%M')
    
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

    if not dados_ordenados:
        elements.append(Paragraph("Nenhum dado encontrado para os filtros selecionados.", styles['Normal']))
    else:
        grafico_labels = []
        grafico_valores = []
        grafico_cores = []

        for cod, d in dados_ordenados:
            perc = round((d['acertos'] / d['total']) * 100, 1) if d['total'] > 0 else 0.0
            
            grafico_labels.append(cod)
            grafico_valores.append(perc)
            
            if perc >= 75: grafico_cores.append('#0d6efd')     
            elif perc >= 50: grafico_cores.append('#198754')   
            elif perc >= 25: grafico_cores.append('#ffc107')   
            else: grafico_cores.append('#dc3545')              

        if grafico_labels:
            plt.figure(figsize=(8, 3.5))
            bars = plt.bar(grafico_labels, grafico_valores, color=grafico_cores, width=0.6)
            
            plt.ylim(0, 115) 
            plt.ylabel('Proficiência (%)', fontsize=10, fontweight='bold', color='#333333')
            
            cor_hex_grafico = cor_pri.hexval().replace('0x', '#') if cor_pri else '#0A2619'
            plt.title('Análise de Desempenho por Descritor', fontsize=12, fontweight='bold', color=cor_hex_grafico, pad=15)
            
            plt.grid(axis='y', linestyle='--', alpha=0.4)
            
            for bar in bars:
                yval = bar.get_height()
                plt.text(bar.get_x() + bar.get_width()/2.0, yval + 2, f'{yval:.1f}%', ha='center', va='bottom', fontsize=8, fontweight='bold', color='#333333')
            
            plt.gca().spines['top'].set_visible(False)
            plt.gca().spines['right'].set_visible(False)
            plt.gca().spines['left'].set_color('#cccccc')
            plt.gca().spines['bottom'].set_color('#cccccc')
            plt.xticks(rotation=45, ha='right', fontsize=8)

            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=150)
            plt.close()
            img_buffer.seek(0)

            img_pdf = RLImage(img_buffer, width=450, height=190)
            elements.append(img_pdf)
            elements.append(Spacer(1, 20))


        data_table = [['CÓDIGO', 'DESCRIÇÃO DA HABILIDADE', 'QTD', '% ACERTO', 'NÍVEL']]

        for cod, d in dados_ordenados:
            perc = round((d['acertos'] / d['total']) * 100, 1) if d['total'] > 0 else 0.0
            
            if perc >= 75: 
                cor_nivel = colors.HexColor('#0d6efd'); nivel_txt = "ADEQUADO"
            elif perc >= 50: 
                cor_nivel = colors.HexColor('#198754'); nivel_txt = "INTERMED."
            elif perc >= 25:
                cor_nivel = colors.HexColor('#d97706'); nivel_txt = "CRÍTICO" 
            else:
                cor_nivel = colors.HexColor('#dc3545'); nivel_txt = "MUITO CRÍTICO"
            
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
    
    filename = "Relatorio_Proficiencia.pdf"
    if aluno_id and resultados.exists():
        filename = f"Relatorio_{resultados.first().matricula.aluno.nome_completo.split()[0]}.pdf"
        
    return FileResponse(buffer, as_attachment=True, filename=filename)

@login_required
def api_filtrar_alunos(request):
    turma_id = request.GET.get('turma_id')
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
    
    resultados = Resultado.objects.filter(matricula__aluno=aluno).select_related('avaliacao', 'avaliacao__alocacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    labels_evo = [res.avaliacao.titulo[:15] + '...' for res in resultados] 
    dados_evo = [float(res.percentual) for res in resultados]
    
    media_geral = sum(dados_evo) / len(dados_evo) if dados_evo else 0
    
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
    
    itens = ItemGabarito.objects.filter(avaliacao=avaliacao).select_related('descritor').order_by('numero')
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
            'aluno': res.matricula.aluno, 
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
    resultados = Resultado.objects.filter(matricula__aluno=aluno).select_related('avaliacao', 'avaliacao__alocacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    matricula_atual = Matricula.objects.filter(aluno=aluno, status='CURSANDO').last()
    nome_turma = matricula_atual.turma.nome if matricula_atual else "Sem Turma"

    dados_grafico = [] 
    dados_tabela = []
    soma_notas = 0
    ultima_nota = 0
    nota_anterior = 0
    
    if resultados.exists():
        for i, res in enumerate(resultados):
            nota_aluno = round(res.percentual / 10, 1)
            
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
                res.avaliacao.titulo[:22],
                res.avaliacao.alocacao.disciplina.nome[:15] if res.avaliacao.alocacao else "-",
                str(nota_aluno),
                str(nota_turma),
                status
            ])

            if i == len(resultados) - 1: ultima_nota = nota_aluno
            if i == len(resultados) - 2: nota_anterior = nota_aluno
            
        media_geral = round(soma_notas / len(resultados), 1)
    else:
        media_geral = 0.0

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

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    COR_DEEP = colors.HexColor("#1e293b") 
    COR_ACCENT = colors.HexColor("#3b82f6") 
    COR_LIGHT = colors.HexColor("#f1f5f9") 
    COR_TEXT = colors.HexColor("#334155") 
    COR_SUCCESS = colors.HexColor("#10b981")
    COR_DANGER = colors.HexColor("#ef4444")

    p = c.beginPath()
    p.moveTo(0, height)
    p.lineTo(width, height)
    p.lineTo(width, height - 120)
    p.curveTo(width, height - 120, width/2, height - 200, 0, height - 120)
    p.close()
    c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.2))
    c.drawPath(p, fill=1, stroke=0)

    p2 = c.beginPath()
    p2.moveTo(0, height)
    p2.lineTo(width, height)
    p2.lineTo(width, height - 110)
    p2.curveTo(width, height - 110, width/2, height - 160, 0, height - 110)
    p2.close()
    c.setFillColor(COR_DEEP)
    c.drawPath(p2, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(40, height - 60, "RELATÓRIO DE DESEMPENHO")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 80, "SAMI EDUCACIONAL • Acompanhamento Integrado")
    
    c.roundRect(width - 100, height - 70, 60, 25, 6, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width - 70, height - 64, str(datetime.now().year))

    y_info = height - 190
    
    c.setStrokeColor(COR_ACCENT)
    c.setFillColor(colors.white)
    c.circle(70, y_info, 35, fill=1, stroke=1)
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(70, y_info - 8, aluno.nome_completo[0])
    
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(120, y_info + 10, aluno.nome_completo[:35])
    c.setFillColor(COR_TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(120, y_info - 10, f"Matrícula: #{aluno.id}  •  Turma: {nome_turma}")
    
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

    y_graph_top = y_info - 80
    graph_h = 100 
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y_graph_top, "Evolução do Bimestre")
    
    y_base = y_graph_top - graph_h - 20
    center_x = width / 2
    
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
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    if avaliacao.matricula: 
        matriculas = [avaliacao.matricula]
    else:
        matriculas = Matricula.objects.filter(
            turma=avaliacao.alocacao.turma, 
            status='CURSANDO'
        ).select_related('aluno').order_by('aluno__nome_completo')
    
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
            
            c.setStrokeColor(colors.black)
            c.setLineWidth(1)
            c.setDash([2, 4])
            c.rect(pos_x, pos_y, card_w, card_h, stroke=1, fill=0)
            c.setDash([])

            c.setFillColor(colors.black)
            marker_size = 15
            c.rect(pos_x + 10, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + card_h - 10 - marker_size, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + 10, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)
            c.rect(pos_x + card_w - 10 - marker_size, pos_y + 10, marker_size, marker_size, fill=1, stroke=0)

            qr_data = f"A{avaliacao.id}-M{mat.id}" 
            
            qr = qrcode.QRCode(box_size=2, border=0)
            qr.add_data(qr_data)
            qr.make(fit=True)
            img_qr = qr.make_image(fill_color="black", back_color="white")
            qr_img_reader = ImageReader(img_qr._img)
            
            c.drawImage(qr_img_reader, pos_x + card_w - 70, pos_y + 20, width=50, height=50)
            
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(pos_x + 35, pos_y + card_h - 25, "CARTÃO RESPOSTA")
            
            c.setFont("Helvetica", 9)
            c.drawString(pos_x + 35, pos_y + card_h - 45, f"Aluno: {aluno.nome_completo[:25]}")
            c.drawString(pos_x + 35, pos_y + card_h - 58, f"Prova: {avaliacao.titulo[:25]}")
            
            c.setFont("Helvetica", 8)
            c.drawString(pos_x + 35, pos_y + card_h - 70, f"Turma: {mat.turma.nome} | Matrícula: {mat.id}")
            
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

    def processar_nota(valor_str):
        if not valor_str or valor_str.strip() == '':
            return None
        try:
            val = float(valor_str.replace(',', '.'))
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
    planos_para_importar = [] 

    for b in range(1, 5):
        dados_kanban[b] = {'TODO': [], 'DOING': [], 'DONE': []}

    if turma_id:
        turma = get_object_or_404(Turma, id=turma_id)
        disciplina_obj = Disciplina.objects.filter(nome=disciplina_selecionada).first()
        prof_sis = get_professor_sistema()
        
        aloc, _ = Alocacao.objects.get_or_create(
            turma=turma, 
            disciplina=disciplina_obj, 
            defaults={'professor': prof_sis}
        )

        plano, created = PlanoEnsino.objects.get_or_create(
            alocacao=aloc, 
            defaults={'ano_letivo': timezone.now().year}
        )

        planos_para_importar = PlanoEnsino.objects.filter(
            alocacao__disciplina__nome=disciplina_selecionada,
            ano_letivo=timezone.now().year
        ).exclude(id=plano.id)

        if request.method == 'POST':
            acao = request.POST.get('acao')

            if 'arquivo_plano' in request.FILES:
                plano.arquivo = request.FILES['arquivo_plano']
                plano.save()
                messages.success(request, "Arquivo anexado com sucesso!")
            
            elif acao == 'importar':
                plano_origem_id = request.POST.get('plano_origem_id')
                if plano_origem_id:
                    plano_origem = PlanoEnsino.objects.get(id=plano_origem_id)
                    for topico in plano_origem.topicos.all():
                        TopicoPlano.objects.create(
                            plano=plano,
                            bimestre=topico.bimestre,
                            conteudo=topico.conteudo,
                            status='TODO', 
                            data_prevista=None 
                        )
                    messages.success(request, f"Tópicos importados da turma {plano_origem.alocacao.turma.nome}!")

            elif acao == 'criar':
                conteudo = request.POST.get('conteudo')
                bimestre = int(request.POST.get('bimestre'))
                data_str = request.POST.get('data_prevista') 
                
                if conteudo:
                    TopicoPlano.objects.create(
                        plano=plano, 
                        bimestre=bimestre, 
                        conteudo=conteudo, 
                        status='TODO',
                        data_prevista=data_str if data_str else None
                    )
                    messages.success(request, "Tópico criado!")

            elif acao == 'editar':
                topico_id = request.POST.get('topico_id')
                topico = get_object_or_404(TopicoPlano, id=topico_id)
                topico.conteudo = request.POST.get('conteudo')
                
                data_str = request.POST.get('data_prevista') 
                topico.data_prevista = data_str if data_str else None
                
                topico.save()
                messages.success(request, "Tópico atualizado!")

            elif acao == 'excluir':
                topico_id = request.POST.get('topico_id')
                TopicoPlano.objects.filter(id=topico_id).delete()
                messages.warning(request, "Tópico removido.")
            
            return redirect(f"{request.path}?turma={turma_id}&disciplina={disciplina_selecionada}")

        topicos = plano.topicos.all().order_by('data_prevista', 'id') 
        for t in topicos:
            dados_kanban[t.bimestre][t.status].append(t)

    return render(request, 'core/plano_anual.html', {
        'turmas': turmas,
        'disciplinas': disciplinas_qs,
        'turma_selecionada_id': int(turma_id) if turma_id else None,
        'disciplina_atual': disciplina_selecionada,
        'plano': plano,
        'dados_kanban': dados_kanban,
        'planos_para_importar': planos_para_importar 
    })

@login_required
def imprimir_plano_pdf(request, plano_id):
    plano = get_object_or_404(PlanoEnsino, id=plano_id)
    
    topicos_por_bimestre = {1: [], 2: [], 3: [], 4: []}
    for t in plano.topicos.all().order_by('bimestre', 'id'):
        topicos_por_bimestre[t.bimestre].append(t)

    html_string = render_to_string('core/relatorios/plano_pdf.html', {
        'plano': plano,
        'topicos_por_bimestre': topicos_por_bimestre,
        'data_geracao': timezone.now()
    })

    result = BytesIO()
    
    pdf = pisa.pisaDocument(BytesIO(html_string.encode("UTF-8")), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Plano_{plano.alocacao.disciplina.nome}.pdf"'
        return response
    
    return HttpResponse("Erro ao gerar PDF", status=500)

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
    filtro_matriz = request.GET.get('matriz')

    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'excluir':
            desc_id = request.POST.get('descritor_id')
            Descritor.objects.filter(id=desc_id).delete()
            messages.success(request, 'Item removido com sucesso.')
            
        elif acao == 'salvar':
            desc_id = request.POST.get('descritor_id')
            disciplina_id = request.POST.get('disciplina')
            matriz = request.POST.get('matriz')
            codigo = request.POST.get('codigo').upper()
            descricao = request.POST.get('descricao')
            pai_id = request.POST.get('descritor_pai')
            
            dados = {
                'disciplina_id': disciplina_id, 
                'matriz': matriz,
                'codigo': codigo, 
                'descricao': descricao,
                'descritor_pai_id': pai_id if pai_id else None
            }
            
            if desc_id:
                d = Descritor.objects.get(id=desc_id)
                for k, v in dados.items(): setattr(d, k, v)
                d.save()
                messages.success(request, 'Atualizado!')
            else:
                Descritor.objects.create(**dados)
                messages.success(request, 'Criado!')
                
        return redirect(f"{request.path}?disciplina={filtro_disc or ''}&matriz={filtro_matriz or ''}")

    descritores_base = Descritor.objects.filter(descritor_pai__isnull=True).order_by('codigo')
    
    if filtro_disc:
        descritores_base = descritores_base.filter(disciplina_id=filtro_disc)
    if filtro_matriz:
        descritores_base = descritores_base.filter(matriz=filtro_matriz)

    disciplinas_ativas = Disciplina.objects.all().order_by('nome')
    arvore = []
    
    for disc in disciplinas_ativas:
        pais_da_disc = descritores_base.filter(disciplina=disc)
        if pais_da_disc.exists() or not filtro_disc: 
            arvore.append({
                'disciplina': disc,
                'pais': pais_da_disc
            })

    context = {
        'arvore': arvore,
        'todas_disciplinas': disciplinas_ativas, 
        'filtro_atual_disc': int(filtro_disc) if filtro_disc else '',
        'filtro_atual_matriz': filtro_matriz or '',
        'todos_pais_opts': Descritor.objects.filter(descritor_pai__isnull=True).order_by('disciplina', 'codigo')
    }
    return render(request, 'core/gerenciar_descritores.html', context)

@login_required
def upload_correcao_cartao(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    if request.method == 'POST' and request.FILES.get('foto_cartao'):
        foto = request.FILES['foto_cartao']
        path = f"media/temp/{foto.name}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb+') as destination:
            for chunk in foto.chunks():
                destination.write(chunk)
        pass 

    matriculas = Matricula.objects.filter(turma=avaliacao.alocacao.turma, status='CURSANDO')
    return render(request, 'core/professor/upload_cartao.html', {'avaliacao': avaliacao, 'matriculas': matriculas})

@csrf_exempt 
def api_ler_cartao(request):
    if request.method == 'POST' and request.FILES.get('foto'):
        path = ""
        try:
            
            foto = request.FILES['foto']
            avaliacao_id = request.POST.get('avaliacao_id')
            
            nome_unico = f"{uuid.uuid4().hex}_{foto.name}"
            path = f"media/temp/{nome_unico}"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb+') as destination:
                for chunk in foto.chunks():
                    destination.write(chunk)

            qtd_questoes = 10
            if avaliacao_id:
                qtd = ItemGabarito.objects.filter(avaliacao_id=avaliacao_id).count()
                if qtd > 0: qtd_questoes = qtd

            scanner = OMRScanner()
            resultado = scanner.processar_cartao(path, qtd_questoes=qtd_questoes)
            
            if resultado.get('qr_code'):
                try:
                    codigo = resultado['qr_code'] 
                    partes = codigo.split('-') 
                    
                    for p in partes:
                        if p.startswith('M'):
                            matricula_id = int(p[1:])
                            try:
                                mat = Matricula.objects.get(id=matricula_id)
                                resultado['matricula_detected_id'] = mat.id 
                                resultado['aluno_nome'] = mat.aluno.nome_completo
                            except Matricula.DoesNotExist:
                                print(f"Matrícula {matricula_id} não encontrada.")

                        elif p.startswith('U'):
                            aluno_id = int(p[1:])
                            resultado['aluno_detectado_id'] = aluno_id
                            
                except Exception as e:
                    print(f"Erro ao interpretar QR Code '{codigo}': {e}")

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
        aluno = request.user.aluno
        resultados = Resultado.objects.filter(matricula__aluno=aluno).order_by('-avaliacao__data_aplicacao')
    except:
        return redirect('dashboard')

    media_geral = 0
    if resultados.exists():
        notas_validas = [r.percentual for r in resultados if r.percentual is not None]
        if notas_validas:
            media_geral = sum(notas_validas) / len(notas_validas)

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
    if not request.user.is_superuser:
        messages.error(request, "Apenas administradores podem realizar esta ação.")
        return redirect('dashboard')

    from django.utils.text import slugify
    
    alunos_sem_acesso = Aluno.objects.filter(usuario__isnull=True)
    criados = 0

    for aluno in alunos_sem_acesso:
        try:
            partes_nome = slugify(aluno.nome_completo).split('-')
            
            if len(partes_nome) >= 2:
                username_base = f"{partes_nome[0]}.{partes_nome[-1]}" 
            else:
                username_base = partes_nome[0] 
            
            username = username_base
            contador = 1
            from django.contrib.auth.models import User
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{contador}"
                contador += 1

            password = "Mudar123" 
            if aluno.cpf:
                senha_cpf = aluno.cpf.replace('.', '').replace('-', '').strip()
                if senha_cpf:
                    password = senha_cpf

            user = User.objects.create_user(username=username, password=password)
            
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


@login_required
def relatorio_ndi_print(request, turma_id, bimestre):
    turma = get_object_or_404(Turma, id=turma_id)
    matriculas = Matricula.objects.filter(turma=turma, status='CURSANDO').select_related('aluno').order_by('aluno__nome_completo')
    
    dados = []
    
    for mat in matriculas:
        ndi = NDI.objects.filter(matricula=mat, bimestre=bimestre).first()
        
        freq = ndi.nota_frequencia if ndi else 0.0
        atv = ndi.nota_atividade if ndi else 0.0
        comp = ndi.nota_comportamento if ndi else 0.0
        pp = ndi.nota_prova_parcial if ndi else 0.0
        pb = ndi.nota_prova_bimestral if ndi else 0.0
        
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
    
    if request.method == 'GET':
        aluno_id = request.GET.get('aluno_id')
        avaliacao_id = request.GET.get('avaliacao_id')
        
        try:
            resultado = Resultado.objects.filter(
                matricula__aluno_id=aluno_id, 
                avaliacao_id=avaliacao_id
            ).first()
            
            dados = {'respostas': {}, 'nota': 0, 'ausente': False}
            
            if resultado:
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

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            aluno_id = data.get('aluno_id')
            avaliacao_id = data.get('avaliacao_id')
            respostas_aluno = data.get('respostas')
            is_ausente = data.get('ausente', False)

            avaliacao = Avaliacao.objects.get(id=avaliacao_id)
            matricula = Matricula.objects.get(aluno_id=aluno_id, turma=avaliacao.alocacao.turma, status='CURSANDO')
            
            gabarito = ItemGabarito.objects.filter(avaliacao=avaliacao).order_by('numero')
            qtd_questoes = gabarito.count()

            if qtd_questoes == 0:
                 return JsonResponse({'sucesso': False, 'erro': 'Defina o gabarito antes de lançar notas.'})
            
            resultado = Resultado.objects.filter(avaliacao=avaliacao, matricula=matricula).first()

            if not resultado:
                resultado = Resultado(
                    avaliacao=avaliacao,
                    matricula=matricula,
                    total_questoes=qtd_questoes,
                    acertos=0,         
                    percentual=0.0     
                )
                resultado.save() 
            else:
                resultado.total_questoes = qtd_questoes
                if resultado.acertos is None: resultado.acertos = 0
                if resultado.percentual is None: resultado.percentual = 0.0
                resultado.save()

            RespostaDetalhada.objects.filter(resultado=resultado).delete()

            if is_ausente:
                resultado.acertos = 0
                resultado.percentual = 0.0
                resultado.save()
                return JsonResponse({'sucesso': True, 'msg': 'Aluno marcado como ausente.'})

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
            
            resultado.acertos = acertos
            resultado.percentual = (acertos / qtd_questoes) * 100
            resultado.save()

            return JsonResponse({'sucesso': True, 'msg': f'Nota salva: {acertos} acertos.'})

        except Exception as e:
            print(f"ERRO CRÍTICO: {e}") 
            return JsonResponse({'sucesso': False, 'erro': f"Erro interno: {str(e)}"})
        

# ==============================================================================
# 🔥 O CORAÇÃO DA SEGURANÇA: ÁREA DO PROFESSOR (SANDBOX)
# ==============================================================================
@login_required
def area_professor(request):
    from django.db.models import Avg, Q, Count
    from datetime import datetime
    
    nome_exibicao = "Professor(a)"

    try:
        perfil = request.user.professor_perfil
        
        # 1. Nome Inteligente
        if perfil.nome_completo: nome_exibicao = perfil.nome_completo.split()[0]
        elif request.user.first_name: nome_exibicao = request.user.first_name
        else: nome_exibicao = request.user.username

        # 2. 🔥 A MÁGICA DA ALOCAÇÃO (FILTRO EXTREMO)
        # O professor SÓ enxerga turmas que estão vinculadas a ele na tabela Alocacao
        turmas = Turma.objects.filter(alocacoes__professor=perfil).distinct().annotate(
            qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'), distinct=True)
        ).order_by('nome')
        
        # Provas atreladas exclusivamente às alocações dele
        avaliacoes_base = Avaliacao.objects.filter(alocacao__professor=perfil)
        provas_recentes = avaliacoes_base.order_by('-data_aplicacao')[:5]

        # 3. Alunos em Alerta (APENAS NAS TURMAS DELE)
        matriculas_prof = Matricula.objects.filter(turma__in=turmas, status='CURSANDO').distinct()
        
        alunos_alerta = matriculas_prof.annotate(
            media_geral=Avg('resultados__percentual')
        ).filter(
            media_geral__lt=60
        ).select_related('aluno', 'turma').order_by('media_geral')[:5]

        # 4. Quadro de Pendências das provas DELE
        provas_pendentes_qs = avaliacoes_base.annotate(
            qtd_resultados=Count('resultado')
        ).filter(
            data_aplicacao__lte=datetime.now().date(), 
            qtd_resultados=0 
        ).order_by('-data_aplicacao')[:3]
        
        total_alunos = matriculas_prof.count()
        kpi_pendencias = provas_pendentes_qs.count()

    except AttributeError:
        # FALLBACK ADMIN
        turmas = Turma.objects.annotate(
            qtd_alunos=Count('alunos_matriculados', filter=Q(alunos_matriculados__status='CURSANDO'))
        ).order_by('nome')
        
        avaliacoes_base = Avaliacao.objects.all()
        provas_recentes = avaliacoes_base.order_by('-data_aplicacao')[:5]
        
        alunos_alerta = Matricula.objects.filter(status='CURSANDO').annotate(
            media_geral=Avg('resultados__percentual')
        ).filter(media_geral__lt=60).select_related('aluno', 'turma').order_by('media_geral')[:5]
        
        provas_pendentes_qs = avaliacoes_base.annotate(
            qtd_resultados=Count('resultado')
        ).filter(data_aplicacao__lte=datetime.now().date(), qtd_resultados=0).order_by('-data_aplicacao')[:3]

        total_alunos = Matricula.objects.filter(status='CURSANDO').count()
        kpi_pendencias = provas_pendentes_qs.count()
        nome_exibicao = request.user.first_name or request.user.username or "Administrador"

    context = {
        'turmas': turmas,
        'provas_recentes': provas_recentes,
        'alunos_alerta': alunos_alerta,             
        'provas_pendentes': provas_pendentes_qs,    
        'kpi_alunos': total_alunos,
        'kpi_pendencias': kpi_pendencias,
        'hoje': datetime.now(),
        'nome_professor': nome_exibicao
    }
    return render(request, 'core/area_professor.html', context)


def login_sucesso_redirect(request):
    user = request.user
    if user.is_superuser or user.is_staff:
        return redirect('dashboard')
    
    if user.groups.filter(name='Professores').exists():
        return redirect('area_professor')
        
    return redirect('dashboard_aluno')


@login_required
def redirecionar_apos_login(request):
    user = request.user
    
    if user.is_superuser or user.is_staff:
        return redirect('dashboard')
    
    if hasattr(user, 'professor_perfil'):
        return redirect('area_professor')

    if hasattr(user, 'aluno'):
        return redirect('dashboard_aluno')
        
    messages.error(request, "Perfil não identificado. Contate a secretaria.")
    return redirect('dashboard')


@login_required
def gerenciar_virada_ano(request):
    mes_atual = timezone.now().month
    ano_atual = timezone.now().year
    
    ano_origem = ano_atual - 1 if mes_atual <= 3 else ano_atual
    ano_destino = ano_origem + 1

    turmas_origem = Turma.objects.filter(ano_letivo=ano_origem).order_by('nome')
    serie_filtro = request.GET.get('serie_filtro') 
    
    alunos_simulados = []
    
    if serie_filtro:
        turmas_da_serie = turmas_origem.filter(nome__icontains=f"{serie_filtro}º") | turmas_origem.filter(nome__icontains=f"{serie_filtro} ANO")
        
        matriculas = Matricula.objects.filter(turma__in=turmas_da_serie, status='CURSANDO').select_related('aluno', 'turma').order_by('aluno__nome_completo')
        
        for mat in matriculas:
            ndis = NDI.objects.filter(matricula=mat)
            soma_ndi = 0; cont_ndi = 0
            for n in ndis:
                n1 = float(n.nota_prova_parcial or 0); n2 = float(n.nota_prova_bimestral or 0)
                if n.nota_prova_parcial is not None or n.nota_prova_bimestral is not None:
                    media_b = (n1 + n2) / 2 if (n1 > 0 and n2 > 0) else max(n1, n2)
                    soma_ndi += media_b; cont_ndi += 1
            media_ndi = (soma_ndi / cont_ndi) if cont_ndi > 0 else 0.0

            media_provas = Resultado.objects.filter(matricula=mat).aggregate(Avg('percentual'))['percentual__avg']
            media_result = (float(media_provas) / 10) if media_provas is not None else 0.0
            
            media_final = max(media_ndi, media_result)
            
            status_sugerido = 'APROVADO' if media_final >= 6.0 else 'REPROVADO'
            
            alunos_simulados.append({
                'id': mat.id,
                'nome': mat.aluno.nome_completo,
                'turma': mat.turma.nome,
                'media': round(media_final, 1),
                'status_sugerido': status_sugerido
            })

    if request.method == 'POST':
        try:
            with transaction.atomic():
                lista_ids = request.POST.getlist('matricula_id')
                count_migrados = 0
                count_formados = 0
                count_saida = 0 
                
                for mat_id in lista_ids:
                    status_decidido = request.POST.get(f'status_{mat_id}')
                    mat = Matricula.objects.get(id=mat_id)
                    
                    mat.status = status_decidido
                    mat.situacao = status_decidido 
                    
                    nota_hidden = request.POST.get(f'nota_{mat_id}')
                    if nota_hidden: mat.media_final = float(nota_hidden.replace(',', '.'))
                    mat.save()
                    
                    if status_decidido in ['TRANSFERIDO', 'ABANDONO']:
                        count_saida += 1
                        continue 
                    
                    nome_turma_atual = mat.turma.nome.upper()
                    
                    if status_decidido == 'APROVADO' and ('3º' in nome_turma_atual or '3 ANO' in nome_turma_atual):
                        mat.status = 'CONCLUIDO'
                        mat.save()
                        count_formados += 1
                        continue 
                        
                    nova_turma_nome = None
                    if status_decidido == 'APROVADO':
                        if '1º' in nome_turma_atual or '1 ANO' in nome_turma_atual:
                            nova_turma_nome = nome_turma_atual.replace('1', '2')
                        elif '2º' in nome_turma_atual or '2 ANO' in nome_turma_atual:
                            nova_turma_nome = nome_turma_atual.replace('2', '3')
                    
                    elif status_decidido == 'REPROVADO':
                        nova_turma_nome = nome_turma_atual 
                        
                    if nova_turma_nome:
                        nova_turma_obj, _ = Turma.objects.get_or_create(
                            nome=nova_turma_nome, 
                            ano_letivo=ano_destino
                        )
                        if not Matricula.objects.filter(aluno=mat.aluno, turma=nova_turma_obj).exists():
                            Matricula.objects.create(
                                aluno=mat.aluno,
                                turma=nova_turma_obj,
                                status='CURSANDO'
                            )
                            count_migrados += 1
                            
                messages.success(request, f"Sucesso! {count_migrados} renovados para {ano_destino}, {count_formados} formados e {count_saida} desligados.")
                return redirect('gerenciar_virada_ano')
                
        except Exception as e:
            messages.error(request, f"Erro ao processar: {e}")

    return render(request, 'core/virada_ano.html', {
        'serie_filtro': serie_filtro,
        'alunos': alunos_simulados,
        'ano_origem': ano_origem,
        'ano_destino': ano_destino
    })

@user_passes_test(is_staff_check)
def cadastrar_professor(request):
    ano_atual = timezone.now().year
    
    if request.method == 'POST':
        form = ProfessorCadastroForm(ano_atual, request.POST)
        if form.is_valid():
            nome_completo = form.cleaned_data['nome_completo'].strip()
            email = form.cleaned_data['email']
            
            nome_limpo = unicodedata.normalize('NFKD', nome_completo).encode('ASCII', 'ignore').decode('utf-8').lower()
            partes_nome = re.findall(r'\b[a-z]+\b', nome_limpo)
            
            if len(partes_nome) > 1:
                base_username = f"{partes_nome[0]}.{partes_nome[-1]}"
            else:
                base_username = partes_nome[0]
                
            username = base_username
            contador = 2
            from django.contrib.auth.models import User
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{contador}"
                contador += 1
                
            senha_padrao = f"Sami@{ano_atual}"
            user = User.objects.create_user(
                username=username,
                email=email,
                password=senha_padrao,
                first_name=partes_nome[0].title(),
                last_name=partes_nome[-1].title() if len(partes_nome) > 1 else ""
            )
            
            professor = form.save(commit=False)
            professor.usuario = user
            professor.save()
            
            # 🔥 CRIAÇÃO DAS ALOCAÇÕES AUTOMATICAMENTE
            disciplinas = form.cleaned_data.get('disciplinas', [])
            turmas = form.cleaned_data.get('turmas', [])
            for t in turmas:
                for d in disciplinas:
                    Alocacao.objects.get_or_create(professor=professor, turma=t, disciplina=d)
            
            mensagem = f"Sucesso! Professor cadastrado. Entregue este acesso: Login: <b>{username}</b> | Senha: <b>{senha_padrao}</b>"
            messages.success(request, mensagem)
            
            return redirect('cadastrar_professor')
    else:
        form = ProfessorCadastroForm(ano_atual)
        
    context = {
        'form': form,
    }
    return render(request, 'core/cadastrar_professor.html', context)