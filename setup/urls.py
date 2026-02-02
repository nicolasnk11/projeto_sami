from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from django.contrib.auth import views as auth_views
from core import views

urlpatterns = [
    # --- ADMIN & AUTH ---
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_redirect, name='home'),
    path('redirecionar/', views.redirecionar_apos_login, name='redirecionar_login'),

    # --- DASHBOARDS ---
    path('dashboard/', views.dashboard, name='dashboard'),
    path('aluno/', views.dashboard_aluno, name='dashboard_aluno'),
    path('painel-gestao/', views.painel_gestao, name='painel_gestao'),
    path('ajuda/', views.central_ajuda, name='central_ajuda'),
    path('professor/', views.area_professor, name='area_professor'),

    # --- AVALIAÇÕES ---
    path('avaliacoes/', views.gerenciar_avaliacoes, name='gerenciar_avaliacoes'),
    path('avaliacoes/nova/', views.criar_avaliacao, name='criar_avaliacao'),
    path('avaliacoes/editar/<int:avaliacao_id>/', views.editar_avaliacao, name='editar_avaliacao'),
    
    # GERAÇÃO DE PROVAS
    path('gerar_prova/', views.gerar_prova_pdf, name='gerar_prova_pdf'),
    path('gerar-prova-inteligente/', views.gerar_prova_pdf, name='gerar_prova_inteligente'),

    path('definir_gabarito/<int:avaliacao_id>/', views.definir_gabarito, name='definir_gabarito'),
    path('montar_prova/<int:avaliacao_id>/', views.montar_prova, name='montar_prova'),
    path('baixar_prova/<int:avaliacao_id>/', views.baixar_prova_existente, name='baixar_prova_existente'),
    path('gerar_cartoes/<int:avaliacao_id>/', views.gerar_cartoes_pdf, name='gerar_cartoes_pdf'),
    
    # --- RELATÓRIOS & ANÁLISE ---
    # Rota geral (sem ID)
    path('relatorio_proficiencia/', views.gerar_relatorio_proficiencia, name='gerar_relatorio_proficiencia'),
    
    # Rota Específica da Avaliação (AQUI ESTAVA O ERRO)
    # Corrigido de views.relatorio_proficiencia para views.gerar_relatorio_proficiencia
    path('relatorio-proficiencia/<int:avaliacao_id>/', views.gerar_relatorio_proficiencia, name='relatorio_proficiencia'),
    
    path('avaliacao/<int:avaliacao_id>/mapa/', views.mapa_calor, name='mapa_calor'),
    path('relatorio-ndi/<int:turma_id>/<int:bimestre>/', views.relatorio_ndi_print, name='relatorio_ndi_print'),

    # --- NOTAS E PLANOS ---
    path('lancar_nota/', views.lancar_nota, name='lancar_nota'),
    path('ndi/', views.gerenciar_ndi, name='gerenciar_ndi'),
    path('plano-aula/', views.plano_anual, name='plano_anual'),
    path('plano/imprimir/<int:plano_id>/', views.imprimir_plano_pdf, name='imprimir_plano_pdf'),

    # --- CADASTROS ---
    path('alunos/', views.gerenciar_alunos, name='gerenciar_alunos'),
    path('turmas/', views.gerenciar_turmas, name='gerenciar_turmas'),
    path('importar_alunos/', views.importar_alunos, name='importar_alunos'),
    path('importar-questoes/', views.importar_questoes, name='importar_questoes'),
    path('banco-questoes/', views.listar_questoes, name='listar_questoes'),
    path('gestao/descritores/', views.gerenciar_descritores, name='gerenciar_descritores'),
    path('gerar-acessos-massa/', views.gerar_acessos_em_massa, name='gerar_acessos_em_massa'),
    path('consultar-acesso/', views.consultar_acesso, name='consultar_acesso'),

    # --- UTILITÁRIOS ---
    path('baixar-modelo/<str:formato>/', views.baixar_modelo, name='baixar_modelo'),
    path('aluno/<int:aluno_id>/perfil/', views.perfil_aluno, name='perfil_aluno'),
    path('aluno/<int:aluno_id>/boletim/', views.gerar_boletim_pdf, name='gerar_boletim_pdf'),
    path('aluno/trocar-senha/', views.trocar_senha_aluno, name='trocar_senha_aluno'),

    # --- APIs ---
    path('api/filtrar-alunos/', views.api_filtrar_alunos, name='api_filtrar_alunos'),
    path('api/filtrar_alunos/', views.api_filtrar_alunos, name='api_filtrar_alunos_alt'),
    path('api/gerar-questao/', views.api_gerar_questao, name='api_gerar_questao'),
    path('api/ler-cartao/', views.api_ler_cartao, name='api_ler_cartao'),
    path('api/mover-topico/<int:id>/<str:novo_status>/', views.mover_topico, name='mover_topico'),
    path('api/toggle-topico/<int:id>/', views.toggle_topico, name='toggle_topico'),
    path('api/lancar-notas-ajax/', views.api_lancar_nota_ajax, name='api_lancar_nota_ajax'),
    path('api/raio-x/', views.api_raio_x, name='api_raio_x'),
    

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)