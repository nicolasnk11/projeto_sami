from django.contrib import admin
from .models import (
    Turma, Aluno, Disciplina, Avaliacao, Resultado, Questao, 
    RespostaDetalhada, ConfiguracaoSistema, ItemGabarito, 
    Descritor, NDI, PlanoEnsino, TopicoPlano,CategoriaAjuda, Tutorial
)

# --- CLASSES PERSONALIZADAS (COM @admin.register) ---

@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ano_letivo')
    search_fields = ('nome',)

@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    list_display = ('nome_completo', 'turma', 'ativo')
    list_filter = ('turma', 'ativo')
    search_fields = ('nome_completo',)

@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ('nome', )
    search_fields = ('nome',)

@admin.register(Avaliacao)
class AvaliacaoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'disciplina', 'turma', 'data_aplicacao')
    list_filter = ('turma', 'disciplina')
    search_fields = ('titulo',)

@admin.register(Resultado)
class ResultadoAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'avaliacao', 'acertos', 'percentual', 'status')
    list_filter = ('status', 'avaliacao__turma', 'avaliacao__disciplina')
    readonly_fields = ('percentual', 'status')

@admin.register(Questao)
class QuestaoAdmin(admin.ModelAdmin):
    list_display = ('enunciado_curto', 'disciplina', 'dificuldade', 'descritor')
    list_filter = ('disciplina', 'dificuldade', 'serie')
    # CORREÇÃO: 'descritor' é ForeignKey, então buscamos pelo código dele
    search_fields = ('enunciado', 'descritor__codigo') 
    
    def enunciado_curto(self, obj):
        return obj.enunciado[:50] + "..."

@admin.register(RespostaDetalhada)
class RespostaDetalhadaAdmin(admin.ModelAdmin):
    list_display = ('get_aluno', 'get_prova', 'get_descritor', 'acertou')
    # CORREÇÃO: Filtros ajustados para relacionamentos
    list_filter = ('acertou', 'questao__descritor', 'resultado__avaliacao__turma')
    
    def get_aluno(self, obj):
        return obj.resultado.aluno.nome_completo
    get_aluno.short_description = 'Aluno'
    
    def get_prova(self, obj):
        return obj.resultado.avaliacao.titulo
    get_prova.short_description = 'Avaliação'

    def get_descritor(self, obj):
        # CORREÇÃO: O descritor agora é um objeto, não um ChoiceField
        if obj.questao and obj.questao.descritor:
            return obj.questao.descritor.codigo
        return "-"
    get_descritor.short_description = 'Descritor'

@admin.register(ConfiguracaoSistema)
class ConfiguracaoSistemaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Singleton: Se já existe 1, não cria outro
        if ConfiguracaoSistema.objects.exists():
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        return False

# --- REGISTROS SIMPLES (SEM CLASSE PERSONALIZADA) ---
# Note que REMOVEMOS Turma, Aluno, Questao, etc daqui pois já estão acima.

admin.site.register(ItemGabarito)
admin.site.register(Descritor)
admin.site.register(NDI)
admin.site.register(PlanoEnsino)
admin.site.register(TopicoPlano)


# core/admin.py

@admin.register(CategoriaAjuda)
class CategoriaAjudaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'icone')

@admin.register(Tutorial)
class TutorialAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'publico', 'data_criacao')
    list_filter = ('publico', 'categoria')
    search_fields = ('titulo', 'descricao')