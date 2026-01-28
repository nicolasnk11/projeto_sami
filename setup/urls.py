from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from core import views
from django.contrib.auth import views as auth_views

# IMPORTANTE: Adicionei 'dashboard_redirect', 'dashboard_aluno' e 'central_ajuda' aqui
from core.views import (
    dashboard, dashboard_redirect, dashboard_aluno, central_ajuda,
    criar_avaliacao, lancar_nota, gerar_prova_pdf, api_filtrar_alunos,
    importar_questoes, definir_gabarito, gerar_relatorio_proficiencia, importar_alunos, gerenciar_alunos, gerenciar_turmas,
    gerenciar_avaliacoes, editar_avaliacao, montar_prova, baixar_prova_existente, painel_gestao, gerenciar_descritores, listar_questoes, perfil_aluno,
    mapa_calor, gerenciar_ndi, plano_anual, toggle_topico, mover_topico, api_gerar_questao, api_ler_cartao, gerar_boletim_pdf, gerar_cartoes_pdf,
    consultar_acesso, logout_view, trocar_senha_aluno
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- ROTAS DE LOGIN E LOGOUT ---
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', logout_view, name='logout'),

    # --- O PORTEIRO (NOVA ROTA PRINCIPAL) ---
    # Quando acessar o site (vazio), o redirect decide se é Aluno ou Professor
    path('', dashboard_redirect, name='home'),

    # --- PAINEIS ESPECÍFICOS ---
    # O dashboard do Professor foi movido para cá
    path('dashboard/', dashboard, name='dashboard'),
    
    # O dashboard do Aluno (NOVO)
    path('aluno/', dashboard_aluno, name='dashboard_aluno'),

    # Central de Ajuda (NOVO)
    path('ajuda/', central_ajuda, name='central_ajuda'),


    # --- ROTAS DO SISTEMA (Mantidas iguais) ---
    path('avaliacoes/', gerenciar_avaliacoes, name='gerenciar_avaliacoes'), 
    path('avaliacoes/nova/', criar_avaliacao, name='criar_avaliacao'),
    
    path('lancar_nota/', lancar_nota, name='lancar_nota'),
    path('gerar_prova/', gerar_prova_pdf, name='gerar_prova_pdf'),
    path('api/filtrar_alunos/', api_filtrar_alunos, name='api_filtrar_alunos'),
    path('definir_gabarito/<int:avaliacao_id>/', definir_gabarito, name='definir_gabarito'),
    path('relatorio_proficiencia/', gerar_relatorio_proficiencia, name='gerar_relatorio_proficiencia'),
    path('importar_alunos/', importar_alunos, name='importar_alunos'),
    path('importar-questoes/', importar_questoes, name='importar_questoes'),
    path('baixar-modelo/<str:formato>/', views.baixar_modelo, name='baixar_modelo'),
    path('alunos/', gerenciar_alunos, name='gerenciar_alunos'),
    path('turmas/', gerenciar_turmas, name='gerenciar_turmas'),
    path('avaliacoes/editar/<int:avaliacao_id>/', editar_avaliacao, name='editar_avaliacao'),
    path('montar_prova/<int:avaliacao_id>/', montar_prova, name='montar_prova'),
    path('baixar_prova/<int:avaliacao_id>/', baixar_prova_existente, name='baixar_prova_existente'),
    path('gestao/', painel_gestao, name='painel_gestao'),
    path('banco-questoes/', listar_questoes, name='listar_questoes'),
    path('aluno/<int:aluno_id>/perfil/', perfil_aluno, name='perfil_aluno'),
    path('avaliacao/<int:avaliacao_id>/mapa/', mapa_calor, name='mapa_calor'),
    path('aluno/<int:aluno_id>/boletim/', gerar_boletim_pdf, name='gerar_boletim_pdf'),
    path('avaliacao/<int:avaliacao_id>/cartoes/', gerar_cartoes_pdf, name='gerar_cartoes_pdf'),
    path('ndi/', gerenciar_ndi, name='gerenciar_ndi'),
    path('plano-aula/', plano_anual, name='plano_anual'),
    path('api/toggle-topico/<int:id>/', toggle_topico, name='toggle_topico'), 
    path('api/mover-topico/<int:id>/<str:novo_status>/', mover_topico, name='mover_topico'),
    path('api/gerar-questao/', api_gerar_questao, name='api_gerar_questao'),
    path('gestao/descritores/', gerenciar_descritores, name='gerenciar_descritores'),
    path('api/ler-cartao/', api_ler_cartao, name='api_ler_cartao'),
    path('consultar-acesso/', consultar_acesso, name='consultar_acesso'),
    path('aluno/trocar-senha/', trocar_senha_aluno, name='trocar_senha_aluno'),
    
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)