from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from core import views
from django.contrib.auth import views as auth_views
from core.views import (dashboard, criar_avaliacao, lancar_nota, gerar_prova_pdf, api_filtrar_alunos,
 importar_questoes, definir_gabarito, gerar_relatorio_proficiencia, importar_alunos, gerenciar_alunos, gerenciar_turmas,
  gerenciar_avaliacoes, editar_avaliacao, montar_prova, baixar_prova_existente, painel_gestao, listar_questoes, listar_descritores, perfil_aluno,
  mapa_calor)


urlpatterns = [
    path('admin/', admin.site.urls),

    # --- ROTAS DE LOGIN E LOGOUT ---
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # --- ROTAS DO SISTEMA ---
    path('', dashboard, name='dashboard'),
    
    # HUB CENTRAL (Sugerido como a base das avaliações)
    path('avaliacoes/', gerenciar_avaliacoes, name='gerenciar_avaliacoes'), 
    
    # CRIAÇÃO (Mantenha apenas uma)
    path('avaliacoes/nova/', criar_avaliacao, name='criar_avaliacao'),
    
    path('lancar_nota/', lancar_nota, name='lancar_nota'),
    path('gerar_prova/', gerar_prova_pdf, name='gerar_prova_pdf'),
    path('api/filtrar_alunos/', api_filtrar_alunos, name='api_filtrar_alunos'),
    path('definir_gabarito/<int:avaliacao_id>/', definir_gabarito, name='definir_gabarito'),
    path('relatorio_proficiencia/', gerar_relatorio_proficiencia, name='gerar_relatorio_proficiencia'),
    path('importar_alunos/', importar_alunos, name='importar_alunos'),
    path('importar-questoes/', views.importar_questoes, name='importar_questoes'),
    path('baixar-modelo/<str:formato>/', views.baixar_modelo, name='baixar_modelo'),
    path('alunos/', gerenciar_alunos, name='gerenciar_alunos'),
    path('turmas/', gerenciar_turmas, name='gerenciar_turmas'),
    path('avaliacoes/editar/<int:avaliacao_id>/', editar_avaliacao, name='editar_avaliacao'),
    path('montar_prova/<int:avaliacao_id>/', montar_prova, name='montar_prova'),
    path('baixar_prova/<int:avaliacao_id>/', views.baixar_prova_existente, name='baixar_prova_existente'),
    path('gestao/', views.painel_gestao, name='painel_gestao'),
    path('banco-questoes/', views.listar_questoes, name='listar_questoes'),
    path('matriz-descritores/', views.listar_descritores, name='listar_descritores'),
    path('aluno/<int:aluno_id>/perfil/', views.perfil_aluno, name='perfil_aluno'),
    path('avaliacao/<int:avaliacao_id>/mapa/', views.mapa_calor, name='mapa_calor'),
    path('aluno/<int:aluno_id>/boletim/', views.gerar_boletim_pdf, name='gerar_boletim_pdf'),
    path('avaliacao/<int:avaliacao_id>/cartoes/', views.gerar_cartoes_pdf, name='gerar_cartoes_pdf'),
    path('ndi/', views.gerenciar_ndi, name='gerenciar_ndi'),
    path('plano-aula/', views.plano_anual, name='plano_anual'),
    path('api/toggle-topico/<int:id>/', views.toggle_topico, name='toggle_topico'), 
    path('api/mover-topico/<int:id>/<str:novo_status>/', views.mover_topico, name='mover_topico'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
