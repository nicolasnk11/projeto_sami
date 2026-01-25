import io
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
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Sum, Q
from django.http import FileResponse, JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
# ReportLab Imports (PDF)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.graphics import renderPDF
from reportlab.lib.units import cm
from datetime import datetime
from reportlab.lib.utils import ImageReader

# Seus Modelos e Forms
from .models import (
    Turma, Resultado, Avaliacao, Questao, Aluno, Disciplina, 
    RespostaDetalhada, ItemGabarito, Descritor, NDI, PlanoEnsino,
    TopicoPlano
)
from .forms import (
    AvaliacaoForm, ResultadoForm, GerarProvaForm, ImportarQuestoesForm, 
    DefinirGabaritoForm, ImportarAlunosForm, AlunoForm
)

from reportlab.lib.utils import ImageReader

# ==============================================================================
# 🖨️ FUNÇÕES AUXILIARES DE PDF (LAYOUT)
# ==============================================================================

def desenhar_cabecalho_prova(p, titulo, turma_nome, disciplina_nome):
    """Desenha o cabeçalho padrão da escola no topo da página PDF."""
    p.setLineWidth(1)
    # Retângulo Principal
    p.rect(30, 750, 535, 80) 
    
    # Nome da Escola (Centralizado)
    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(297, 810, "EEMTI PARQUE MARIA BERNARDO DE CASTRO")
    
    # Título da Prova
    p.setFont("Helvetica-Bold", 10)
    p.drawCentredString(297, 795, f"AVALIAÇÃO DE {disciplina_nome.upper()} - {titulo.upper()}")
    
    # Campos de Preenchimento
    p.setFont("Helvetica", 10)
    
    # Linha 1: Aluno e Número
    p.drawString(40, 775, "ALUNO(A): _______________________________________________________")
    p.drawString(460, 775, "Nº: _______")
    
    # Linha 2: Turma, Data, Nota
    p.drawString(40, 758, f"TURMA: {turma_nome}")
    p.drawString(220, 758, "DATA: _____/_____/_____")
    p.drawString(460, 758, "NOTA: __________")

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
# 📊 DASHBOARD
# ==============================================================================

@login_required
def dashboard(request):
    # 1. Filtros
    serie_id = request.GET.get('serie')
    turma_id = request.GET.get('turma')
    aluno_id = request.GET.get('aluno')
    avaliacao_id = request.GET.get('avaliacao')
    disciplina_id = request.GET.get('disciplina')
    
    resultados = Resultado.objects.all()
    turmas_filtro = Turma.objects.all()
    alunos_filtro = Aluno.objects.none()

    if disciplina_id:
        resultados = resultados.filter(avaliacao__disciplina_id=disciplina_id)
    if serie_id:
        resultados = resultados.filter(avaliacao__turma__nome__startswith=serie_id)
        turmas_filtro = turmas_filtro.filter(nome__startswith=serie_id)
    if turma_id:
        resultados = resultados.filter(avaliacao__turma_id=turma_id)
        alunos_filtro = Aluno.objects.filter(turma_id=turma_id)
    if aluno_id:
        resultados = resultados.filter(aluno_id=aluno_id)
    if avaliacao_id:
        resultados = resultados.filter(avaliacao_id=avaliacao_id)

    # 2. Processamento Geral
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related(
        'questao', 'resultado__aluno', 'item_gabarito', 'item_gabarito__descritor'
    )
    
    if disciplina_id:
        respostas = respostas.filter(
            Q(questao__disciplina_id=disciplina_id) | 
            Q(item_gabarito__descritor__disciplina_id=disciplina_id) |
            Q(item_gabarito__avaliacao__disciplina_id=disciplina_id)
        )

    stats_descritores = {}
    stats_questoes = {}
    calculo_consolidado = {}

    for resp in respostas:
        desc_codigo = "N/D"
        desc_texto_completo = "Não Mapeado"

        if resp.item_gabarito:
            if resp.item_gabarito.descritor:
                d = resp.item_gabarito.descritor
                desc_codigo = d.codigo
                desc_texto_completo = f"{d.codigo} - {d.descricao}"
            else:
                desc_codigo = f"Q.{resp.item_gabarito.numero}"
                desc_texto_completo = f"Questão {resp.item_gabarito.numero}"
        elif resp.questao:
            if resp.questao.descritor:
                desc_codigo = resp.questao.descritor.codigo
                desc_texto_completo = f"{resp.questao.descritor.codigo} - {resp.questao.descritor.descricao}"
            else:
                desc_codigo = "Geral"
                desc_texto_completo = "Geral"

        if desc_codigo not in stats_descritores:
            stats_descritores[desc_codigo] = {'acertos': 0, 'total': 0}
        stats_descritores[desc_codigo]['total'] += 1
        if resp.acertou: stats_descritores[desc_codigo]['acertos'] += 1

        aluno_nome = resp.resultado.aluno.nome_completo
        if desc_texto_completo not in calculo_consolidado:
            calculo_consolidado[desc_texto_completo] = {}
        
        if aluno_nome not in calculo_consolidado[desc_texto_completo]:
            calculo_consolidado[desc_texto_completo][aluno_nome] = {'acertos': 0, 'total': 0}
        
        calculo_consolidado[desc_texto_completo][aluno_nome]['total'] += 1
        if resp.acertou:
            calculo_consolidado[desc_texto_completo][aluno_nome]['acertos'] += 1

        id_questao = f"Q.{resp.item_gabarito.numero}" if resp.item_gabarito else f"ID {resp.questao.id}"
        enunciado = "Sem texto"
        if resp.questao: enunciado = resp.questao.enunciado
        elif resp.item_gabarito and resp.item_gabarito.questao_banco:
            enunciado = resp.item_gabarito.questao_banco.enunciado
        
        chave_q = f"{id_questao}|{desc_codigo}"
        if chave_q not in stats_questoes:
            stats_questoes[chave_q] = {'texto': enunciado, 'acertos': 0, 'total': 0, 'id': id_questao, 'desc': desc_codigo}
        
        stats_questoes[chave_q]['total'] += 1
        if resp.acertou: stats_questoes[chave_q]['acertos'] += 1

    labels_proficiencia = []
    dados_proficiencia = []
    for desc in sorted(stats_descritores.keys()):
        d = stats_descritores[desc]
        if d['total'] > 0:
            perc = (d['acertos'] / d['total']) * 100
            labels_proficiencia.append(desc)
            dados_proficiencia.append(round(perc, 1))

    alunos_adq, alunos_int, alunos_cri, alunos_mui = [], [], [], []
    for res in resultados:
        p = float(res.percentual)
        dados_aluno = {
            'nome': res.aluno.nome_completo, 
            'turma': res.avaliacao.turma.nome,
            'nota': round(p, 1)
        }
        if p > 80: alunos_adq.append(dados_aluno)
        elif p >= 60: alunos_int.append(dados_aluno)
        elif p >= 21: alunos_cri.append(dados_aluno)
        else: alunos_mui.append(dados_aluno)

    dados_pizza = [len(alunos_adq), len(alunos_int), len(alunos_cri), len(alunos_mui)]
    detalhes_pizza_json = json.dumps({
        'Adequado': alunos_adq, 'Intermediário': alunos_int, 
        'Crítico': alunos_cri, 'Muito Crítico': alunos_mui
    })

    mapa_habilidades = {}
    for desc, alunos_data in calculo_consolidado.items():
        mapa_habilidades[desc] = {'adequadas': [], 'criticas': []}
        for aluno, stats in alunos_data.items():
            if stats['total'] > 0:
                aproveitamento = (stats['acertos'] / stats['total']) * 100
                if aproveitamento >= 50:
                    mapa_habilidades[desc]['adequadas'].append(aluno)
                else:
                    mapa_habilidades[desc]['criticas'].append(aluno)
        mapa_habilidades[desc]['adequadas'].sort()
        mapa_habilidades[desc]['criticas'].sort()

    lista_questoes = []
    for k, v in stats_questoes.items():
        if v['total'] > 0:
            perc_acerto = (v['acertos'] / v['total']) * 100
            perc_erro = 100 - perc_acerto
            lista_questoes.append({
                'id': v['id'], 'desc': v['desc'], 'texto': v['texto'],
                'percentual_acerto': round(perc_acerto, 1),
                'percentual_erro': round(perc_erro, 1),
                'total': v['total']
            })
    
    ranking_facil = sorted(lista_questoes, key=lambda x: x['percentual_acerto'], reverse=True)
    ranking_dificil = sorted(lista_questoes, key=lambda x: x['percentual_erro'], reverse=True)

    nome_filtro = "Visão Geral"
    obj_avaliacao = None
    if avaliacao_id: 
        obj_avaliacao = Avaliacao.objects.filter(id=avaliacao_id).first()
        if obj_avaliacao: nome_filtro = f"Prova: {obj_avaliacao.titulo}"
    elif aluno_id: 
        obj = Aluno.objects.filter(id=aluno_id).first()
        if obj: nome_filtro = f"Aluno: {obj.nome_completo}"
    elif turma_id: 
        obj = Turma.objects.filter(id=turma_id).first()
        if obj: nome_filtro = f"Turma: {obj.nome}"
    elif serie_id: nome_filtro = f"{serie_id}º Ano Geral"

    # --- Gráfico de Evolução ---
    labels_evolucao, dados_evolucao = [], []
    avaliacoes_query = Avaliacao.objects.all().order_by('data_aplicacao')
    if disciplina_id: avaliacoes_query = avaliacoes_query.filter(disciplina_id=disciplina_id)
    if turma_id: avaliacoes_query = avaliacoes_query.filter(turma_id=turma_id)
    elif serie_id: avaliacoes_query = avaliacoes_query.filter(turma__nome__startswith=serie_id)
    
    for av in avaliacoes_query:
        res_av = resultados.filter(avaliacao=av)
        if res_av.exists():
            media = sum([r.percentual for r in res_av]) / res_av.count()
            labels_evolucao.append(av.titulo)
            dados_evolucao.append(round(media, 1))

    # 3. DADOS PARA O MAPA DE CALOR (GRID)
    # Só gera se tiver uma avaliação selecionada
    itens_heatmap = []
    matriz_calor = []
    stats_heatmap_footer = []

    if avaliacao_id and obj_avaliacao:
        # Pega as questões ordenadas
        itens_heatmap = ItemGabarito.objects.filter(avaliacao=obj_avaliacao).select_related('descritor').order_by('numero')
        
        # Pega resultados dessa prova (já filtrados acima em 'resultados')
        resultados_heatmap = resultados.select_related('aluno').order_by('aluno__nome_completo')
        total_alunos_heatmap = resultados_heatmap.count() or 1

        for res in resultados_heatmap:
            # Respostas desse aluno
            resps_aluno = RespostaDetalhada.objects.filter(resultado=res)
            mapa_res = {r.item_gabarito_id: r.acertou for r in resps_aluno}
            
            linha_q = []
            acertos_cnt = 0
            for item in itens_heatmap:
                status = mapa_res.get(item.id) # True, False ou None
                linha_q.append({'acertou': status, 'numero': item.numero})
                if status: acertos_cnt += 1
            
            nota_calc = round(res.percentual / 10, 1) if res.percentual else 0.0
            
            matriz_calor.append({
                'aluno': res.aluno,
                'nota': nota_calc,
                'questoes': linha_q
            })

        # Rodapé do Heatmap (% acerto por questão)
        for item in itens_heatmap:
            qtd = RespostaDetalhada.objects.filter(item_gabarito=item, acertou=True).count()
            perc = (qtd / total_alunos_heatmap) * 100
            stats_heatmap_footer.append({'numero': item.numero, 'perc': round(perc)})

    contexto = {
        'total_turmas': Turma.objects.count(),
        'turmas_da_serie': turmas_filtro,
        'alunos_da_turma': alunos_filtro,
        'disciplinas': Disciplina.objects.all(),
        'serie_selecionada': serie_id,
        'turma_selecionada': turma_id,
        'aluno_selecionado': aluno_id,
        'avaliacao_selecionada': avaliacao_id,
        'disciplina_selecionada': disciplina_id,
        'nome_filtro': nome_filtro,
        'dados_pizza': dados_pizza,
        'detalhes_pizza_json': detalhes_pizza_json,
        'labels_evolucao': labels_evolucao,
        'dados_evolucao': dados_evolucao,
        'labels_proficiencia': labels_proficiencia,
        'dados_proficiencia': dados_proficiencia,
        'ranking_facil': ranking_facil, 
        'ranking_dificil': ranking_dificil,
        'mapa_habilidades': mapa_habilidades,
        'ultimos_resultados': resultados.order_by('-id')[:50], 
        'avaliacoes_todas': Avaliacao.objects.all().order_by('-id')[:50], 
        'total_avaliacoes_contagem': Avaliacao.objects.count(),
        
        # Novos dados para o Heatmap
        'itens_heatmap': itens_heatmap,
        'matriz_calor': matriz_calor,
        'stats_heatmap_footer': stats_heatmap_footer
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
def listar_descritores(request):
    descritores = Descritor.objects.all().order_by('codigo')
    return render(request, 'core/listar_descritores.html', {'descritores': descritores})

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

@login_required
def gerar_relatorio_proficiencia(request):
    # Importação necessária para cores (caso não esteja no topo)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4

    # 1. OBTER E LIMPAR INPUTS (A Correção Principal) 🛡️
    aluno_id = request.GET.get('aluno')
    turma_id = request.GET.get('turma')
    serie_id = request.GET.get('serie')

    # Funçãozinha rápida para limpar "None" (texto) e vazios
    def limpar_param(param):
        if param in ['None', 'null', '', None]:
            return None
        return param

    aluno_id = limpar_param(aluno_id)
    turma_id = limpar_param(turma_id)
    serie_id = limpar_param(serie_id)

    # 2. FILTRAGEM INTELIGENTE 🧠
    resultados = Resultado.objects.all()
    subtitulo = "Visão Geral da Escola"
    
    if aluno_id:
        # Se tem aluno, filtra só ele
        aluno = get_object_or_404(Aluno, id=aluno_id)
        resultados = resultados.filter(aluno=aluno)
        subtitulo = f"Aluno(a): {aluno.nome_completo}"
    elif turma_id:
        # Se não tem aluno, mas tem turma, filtra a turma
        turma = get_object_or_404(Turma, id=turma_id)
        resultados = resultados.filter(avaliacao__turma=turma)
        subtitulo = f"Turma: {turma.nome}"
    elif serie_id:
        # Filtro por série (se você usar padrão de nomes como '1º Ano')
        resultados = resultados.filter(avaliacao__turma__nome__startswith=serie_id)
        subtitulo = f"{serie_id}º Ano Geral"

    # Se não houver resultados após o filtro, evita gerar PDF vazio
    if not resultados.exists():
        # Você pode redirecionar ou gerar um PDF avisando
        # Aqui vou deixar passar, mas os gráficos ficarão zerados
        pass

    # ==========================================================
    # DAQUI PARA BAIXO É A GERAÇÃO DO PDF (Visual Mantido)
    # ==========================================================
    
    respostas = RespostaDetalhada.objects.filter(resultado__in=resultados).select_related('item_gabarito__descritor')
    
    # Processamento dos Dados
    stats = {}
    for resp in respostas:
        desc_cod = "Geral"
        desc_texto = "Habilidade Geral"
        
        if resp.item_gabarito and resp.item_gabarito.descritor:
            desc_cod = resp.item_gabarito.descritor.codigo
            desc_texto = resp.item_gabarito.descritor.descricao
        
        if desc_cod not in stats: 
            stats[desc_cod] = {'acertos': 0, 'total': 0, 'texto': desc_texto}
        
        stats[desc_cod]['total'] += 1
        if resp.acertou: stats[desc_cod]['acertos'] += 1

    # Ordenar por código do descritor
    lista_ordenada = sorted(stats.items())

    # Configuração do PDF
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Cores do Sistema SAMI
    COR_PRIMARIA = colors.HexColor("#0f172a") # Dark Slate
    COR_DESTAQUE = colors.HexColor("#3b82f6") # Azul
    COR_CINZA_FUNDO = colors.HexColor("#f1f5f9")
    
    def desenhar_cabecalho():
        # Fundo do Cabeçalho
        p.setFillColor(COR_PRIMARIA)
        p.rect(0, height - 100, width, 100, fill=1, stroke=0)
        
        # Título
        p.setFillColor(colors.white)
        p.setFont("Helvetica-Bold", 18)
        p.drawString(40, height - 40, "SAMI | Relatório de Proficiência")
        
        # Subtítulo
        p.setFont("Helvetica", 12)
        p.setFillColor(colors.lightgrey)
        p.drawString(40, height - 65, subtitulo)
        
        # Data
        data_hoje = datetime.now().strftime("%d/%m/%Y")
        p.drawRightString(width - 40, height - 40, f"Gerado em: {data_hoje}")

    def desenhar_titulos_tabela(y):
        p.setFillColor(COR_CINZA_FUNDO)
        p.rect(40, y - 5, width - 80, 25, fill=1, stroke=0)
        
        p.setFillColor(colors.black)
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y + 2, "CÓDIGO")
        p.drawString(120, y + 2, "DESCRIÇÃO DA HABILIDADE")
        p.drawString(400, y + 2, "NÍVEL")
        p.drawString(480, y + 2, "% ACERTO")

    # Início do Desenho
    desenhar_cabecalho()
    
    y = height - 140
    desenhar_titulos_tabela(y)
    y -= 30

    p.setFont("Helvetica", 10)

    for codigo, dados in lista_ordenada:
        # Verifica quebra de página
        if y < 50:
            p.showPage()
            desenhar_cabecalho()
            y = height - 140
            desenhar_titulos_tabela(y)
            y -= 30
            p.setFont("Helvetica", 10)

        total = dados['total']
        acertos = dados['acertos']
        perc = (acertos / total) * 100 if total > 0 else 0
        
        # Linha Zebrada
        p.setStrokeColor(colors.lightgrey)
        p.setLineWidth(0.5)
        p.line(40, y - 5, width - 40, y - 5)

        # 1. Código
        p.setFillColor(colors.black)
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, codigo)
        
        # 2. Descrição (Truncada para não vazar)
        descricao = dados['texto'][:55] + "..." if len(dados['texto']) > 55 else dados['texto']
        p.setFont("Helvetica", 9)
        p.setFillColor(colors.darkgrey)
        p.drawString(120, y, descricao)

        # 3. Barra de Progresso Visual
        bar_width = 60
        perc_width = (perc / 100) * bar_width
        
        # Cor da barra baseada no desempenho
        cor_barra = colors.red
        nivel_txt = "CRÍTICO"
        if perc >= 80: 
            cor_barra = colors.green
            nivel_txt = "ADEQUADO"
        elif perc >= 60: 
            cor_barra = colors.orange
            nivel_txt = "INTERM."
        
        # Fundo da barra (cinza)
        p.setFillColor(colors.lightgrey)
        p.roundRect(400, y, bar_width, 8, 2, fill=1, stroke=0)
        
        # Preenchimento da barra (colorido)
        if perc > 0:
            p.setFillColor(cor_barra)
            p.roundRect(400, y, perc_width, 8, 2, fill=1, stroke=0)

        # 4. Percentual Numérico
        p.setFillColor(colors.black)
        p.setFont("Helvetica-Bold", 10)
        p.drawRightString(540, y, f"{perc:.1f}%")
        
        y -= 25 # Pula para próxima linha

    # Rodapé
    p.setFont("Helvetica", 8)
    p.setFillColor(colors.grey)
    p.drawCentredString(width / 2, 30, "SAMI - Sistema de Gestão Escolar Inteligente")

    p.showPage()
    p.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'relatorio_proficiencia_{datetime.now().strftime("%Y%m%d")}.pdf')

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

@login_required
def gerar_boletim_pdf(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    resultados = Resultado.objects.filter(aluno=aluno).select_related('avaliacao', 'avaliacao__disciplina').order_by('avaliacao__data_aplicacao')
    
    # --- 1. DADOS ---
    dados_grafico = [] 
    dados_tabela = []
    soma_notas = 0
    
    if resultados.exists():
        for res in resultados:
            nota_aluno = round(res.percentual / 10, 1)
            media_turma = Resultado.objects.filter(avaliacao=res.avaliacao).aggregate(Avg('percentual'))['percentual__avg'] or 0
            nota_turma = round(media_turma / 10, 1)
            
            dados_grafico.append({
                'aluno': nota_aluno,
                'turma': nota_turma,
                'label': res.avaliacao.data_aplicacao.strftime("%d/%m")
            })
            soma_notas += nota_aluno
            
            status = "ACIMA" if nota_aluno >= nota_turma else "ABAIXO"
            if nota_aluno < 6: status = "CRÍTICO"
            
            dados_tabela.append([
                res.avaliacao.data_aplicacao.strftime("%d/%m"),
                res.avaliacao.titulo[:25],
                res.avaliacao.disciplina.nome[:15],
                str(nota_aluno),
                str(nota_turma),
                status
            ])
        media_geral = round(soma_notas / len(resultados), 1)
    else:
        media_geral = 0.0

    # --- 2. SETUP VISUAL ---
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Paleta Premium
    COR_DEEP = colors.HexColor("#1e293b") # Azul Petróleo Escuro
    COR_ACCENT = colors.HexColor("#3b82f6") # Azul Royal
    COR_LIGHT = colors.HexColor("#f1f5f9") # Cinza Claro
    COR_TEXT = colors.HexColor("#334155") # Cinza Texto
    COR_SUCCESS = colors.HexColor("#10b981")
    COR_DANGER = colors.HexColor("#ef4444")

    # --- 3. CABEÇALHO DUPLA ONDA ---
    # Onda Fundo (Accent)
    p = c.beginPath()
    p.moveTo(0, height)
    p.lineTo(width, height)
    p.lineTo(width, height - 120)
    p.curveTo(width, height - 120, width/2, height - 200, 0, height - 120)
    p.close()
    c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.2)) # Azul Transparente
    c.drawPath(p, fill=1, stroke=0)

    # Onda Principal (Deep)
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
    c.setFillColor(colors.white)
    c.roundRect(width - 100, height - 70, 60, 25, 6, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width - 70, height - 64, str(datetime.now().year))

    # --- 4. INFO DO ALUNO (CLEAN) ---
    y_info = height - 190
    
    # Foto Placeholder
    c.setStrokeColor(COR_ACCENT)
    c.setFillColor(colors.white)
    c.circle(70, y_info, 35, fill=1, stroke=1)
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(70, y_info - 8, aluno.nome_completo[0])
    
    # Nome e Turma
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(120, y_info + 10, aluno.nome_completo)
    c.setFillColor(COR_TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(120, y_info - 10, f"Matrícula: #{aluno.id}  •  Turma: {aluno.turma.nome}")
    
    # Card de Nota (Direita)
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

    # --- 5. GRÁFICO (LAYOUT MELHORADO) ---
    y_graph_top = y_info - 80
    graph_h = 130
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y_graph_top, "Análise Visual")
    
    y_base = y_graph_top - graph_h - 20
    center_x = width / 2
    
    # Linha de base do gráfico
    c.setStrokeColor(colors.lightgrey)
    c.setLineWidth(1)
    c.line(40, y_base, width - 40, y_base)

    if len(dados_grafico) == 1:
        # --- MODO 1: BARRAS LARGAS CENTRALIZADAS ---
        dado = dados_grafico[0]
        bar_w = 80 # Barras mais largas
        gap = 40   # Espaço entre elas
        
        # Posições X calculadas a partir do centro
        x_aluno = center_x - bar_w - (gap/2)
        x_turma = center_x + (gap/2)
        
        # Altura proporcional
        h_aluno = (dado['aluno'] / 10) * graph_h
        h_turma = (dado['turma'] / 10) * graph_h
        
        # Barra Aluno
        c.setFillColor(COR_ACCENT)
        c.roundRect(x_aluno, y_base, bar_w, h_aluno, 4, fill=1, stroke=0)
        # Label Aluno
        c.setFillColor(COR_DEEP)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x_aluno + bar_w/2, y_base + h_aluno + 5, str(dado['aluno']))
        c.setFont("Helvetica", 9)
        c.drawCentredString(x_aluno + bar_w/2, y_base - 15, "Você")
        
        # Barra Turma
        c.setFillColor(colors.HexColor("#cbd5e1"))
        c.roundRect(x_turma, y_base, bar_w, h_turma, 4, fill=1, stroke=0)
        # Label Turma
        c.setFillColor(COR_DEEP)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x_turma + bar_w/2, y_base + h_turma + 5, str(dado['turma']))
        c.setFont("Helvetica", 9)
        c.drawCentredString(x_turma + bar_w/2, y_base - 15, "Turma")
        
        # Subtítulo da prova
        c.setFillColor(colors.grey)
        c.setFont("Helvetica-Oblique", 9)
        c.drawCentredString(center_x, y_base - 35, f"Referente à avaliação: {dado['label']}")

    elif len(dados_grafico) > 1:
        # --- MODO 2: ÁREA CHART CONECTADO ---
        # (Código similar ao anterior, mas ajustado)
        graph_width = 450
        x_start = 65
        step_x = graph_width / (len(dados_grafico) - 1)
        coords_x = [x_start + (i * step_x) for i in range(len(dados_grafico))]
        
        # Grid horizontal
        c.setStrokeColor(colors.HexColor("#f1f5f9"))
        for i in range(5):
            ly = y_base + (i * (graph_h/4))
            c.line(x_start, ly, x_start + graph_width, ly)
            c.setFillColor(colors.grey); c.setFont("Helvetica", 7)
            c.drawRightString(x_start - 5, ly - 2, str(i * 2.5))

        # Área Aluno
        p = c.beginPath()
        p.moveTo(coords_x[0], y_base)
        for i in range(len(dados_grafico)):
            y_pt = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
            p.lineTo(coords_x[i], y_pt)
        p.lineTo(coords_x[-1], y_base)
        p.close()
        c.setFillColor(colors.Color(59/255, 130/255, 246/255, alpha=0.15))
        c.drawPath(p, fill=1, stroke=0)
        
        # Linha Aluno
        c.setStrokeColor(COR_ACCENT); c.setLineWidth(2.5)
        path = c.beginPath()
        for i in range(len(dados_grafico)):
            y_pt = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
            if i==0: path.moveTo(coords_x[i], y_pt)
            else: path.lineTo(coords_x[i], y_pt)
        c.drawPath(path, stroke=1, fill=0)
        
        # Pontos
        for i in range(len(dados_grafico)):
            cx = coords_x[i]
            cy = y_base + (dados_grafico[i]['aluno'] / 10 * graph_h)
            c.setFillColor(colors.white); c.setStrokeColor(COR_ACCENT); c.setLineWidth(2)
            c.circle(cx, cy, 4, fill=1, stroke=1)
            c.setFillColor(colors.grey); c.setFont("Helvetica", 8)
            c.drawCentredString(cx, y_base - 12, dados_grafico[i]['label'])

    # --- 6. TABELA ESTILIZADA ---
    y_table_title = y_base - 60
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y_table_title, "Histórico Detalhado")
    
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
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('TEXTCOLOR', (0,1), (-1,-1), COR_TEXT),
        ('FONTSIZE', (0,1), (-1,-1), 9),
    ]
    
    for idx, row in enumerate(dados_tabela):
        linha = idx + 1
        nota = float(row[3])
        cor = COR_SUCCESS if nota >= 6 else COR_DANGER
        estilo.append(('TEXTCOLOR', (3, linha), (3, linha), cor))
        estilo.append(('FONTNAME', (3, linha), (3, linha), 'Helvetica-Bold'))
        
        status_cor = COR_SUCCESS if row[5] == "ACIMA" else COR_DANGER if row[5] == "CRÍTICO" else colors.orange
        estilo.append(('TEXTCOLOR', (5, linha), (5, linha), status_cor))
        estilo.append(('FONTSIZE', (5, linha), (5, linha), 7))

    t.setStyle(TableStyle(estilo))
    w_t, h_t = t.wrapOn(c, width, height)
    t.drawOn(c, 40, y_table_title - h_t - 15)

    # --- 7. PARECER (CARD MODERNO) ---
    y_footer = 50
    # Borda colorida lateral
    cor_borda = COR_SUCCESS if media_geral >= 6 else COR_DANGER
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.roundRect(40, y_footer, width - 80, 60, 6, fill=1, stroke=0) # Fundo
    
    c.setFillColor(cor_borda)
    c.roundRect(40, y_footer, 6, 60, 0, fill=1, stroke=0) # Faixa lateral
    
    c.setFillColor(COR_DEEP)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(55, y_footer + 35, "DIAGNÓSTICO AUTOMÁTICO:")
    
    c.setFont("Helvetica", 10)
    c.setFillColor(COR_TEXT)
    msg = ""
    if media_geral >= 8: msg = "Excelente! Aluno demonstra domínio superior."
    elif media_geral >= 6: msg = "Satisfatório. O desempenho atende às expectativas base."
    else: msg = "Atenção. O aluno encontra-se abaixo da média e requer reforço."
    c.drawString(210, y_footer + 35, msg)
    
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawString(55, y_footer + 15, "Este documento é apenas informativo e não substitui o histórico escolar oficial.")

    c.showPage()
    c.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'Boletim_{aluno.nome_completo}.pdf')



@login_required
def gerar_cartoes_pdf(request, avaliacao_id):
    avaliacao = get_object_or_404(Avaliacao, id=avaliacao_id)
    
    # --- CORREÇÃO AQUI ---
    # Verifica se a avaliação é individual ou da turma
    if avaliacao.aluno:
        # Se tiver aluno vinculado, gera SÓ pra ele
        alunos = [avaliacao.aluno]
    else:
        # Se não, gera pra turma toda
        alunos = Aluno.objects.filter(turma=avaliacao.turma).order_by('nome_completo')
    
    # ... (Restante do código de desenho dos cartões permanece IDÊNTICO) ...
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
    
    aluno_idx = 0
    total_alunos = len(alunos)
    
    while aluno_idx < total_alunos:
        for pos_x, pos_y in positions:
            if aluno_idx >= total_alunos: break
            aluno = alunos[aluno_idx]
            
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

            # QR Code
            qr_data = f"A{avaliacao.id}-U{aluno.id}"
            qr = qrcode.make(qr_data)
            qr_img = ImageReader(qr._img)
            c.drawImage(qr_img, pos_x + card_w - 80, pos_y + card_h - 90, width=60, height=60)
            
            # Texto
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(pos_x + 35, pos_y + card_h - 25, "CARTÃO RESPOSTA")
            c.setFont("Helvetica", 8)
            c.drawString(pos_x + 35, pos_y + card_h - 40, f"Aluno: {aluno.nome_completo[:25]}")
            c.drawString(pos_x + 35, pos_y + card_h - 52, f"Prova: {avaliacao.titulo[:25]}")
            c.drawString(pos_x + 35, pos_y + card_h - 64, f"Turma: {aluno.turma.nome}")
            
            # Bolinhas
            y_start = pos_y + card_h - 90
            x_col1 = pos_x + 30
            x_col2 = pos_x + card_w/2 + 10
            total_questoes = ItemGabarito.objects.filter(avaliacao=avaliacao).count() or 10
            
            c.setFont("Helvetica", 8)
            for q_num in range(1, total_questoes + 1):
                if q_num <= 10:
                    curr_x = x_col1
                    curr_y = y_start - ((q_num - 1) * 15)
                else:
                    curr_x = x_col2
                    curr_y = y_start - ((q_num - 11) * 15)
                
                c.drawString(curr_x, curr_y, str(q_num).zfill(2))
                opcoes = ['A', 'B', 'C', 'D', 'E']
                for i, opt in enumerate(opcoes):
                    bubble_x = curr_x + 25 + (i * 15)
                    bubble_y = curr_y + 3
                    c.circle(bubble_x, bubble_y, 5, stroke=1, fill=0)
                    c.setFont("Helvetica", 5)
                    c.drawCentredString(bubble_x, bubble_y - 1.5, opt)
                    c.setFont("Helvetica", 8)

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