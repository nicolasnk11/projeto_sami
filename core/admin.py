from django.contrib import admin
from .models import (
    Turma, Aluno, Disciplina, Avaliacao, Resultado, Questao, 
    RespostaDetalhada, ConfiguracaoSistema, ItemGabarito, 
    Descritor, NDI, PlanoEnsino, TopicoPlano, CategoriaAjuda, Tutorial,
    Matricula  # <--- Importante: Adicionei Matrícula
)

# --- CLASSES PERSONALIZADAS ---

@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ano_letivo')
    list_filter = ('ano_letivo',)
    search_fields = ('nome',)

@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    # CORREÇÃO: Removemos 'turma' e 'ativo' daqui, pois agora pertencem à Matrícula
    list_display = ('nome_completo', 'cpf', 'data_nascimento')
    search_fields = ('nome_completo', 'cpf')

@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    # NOVA CLASSE: Aqui é onde você vê a Turma e se o aluno está Ativo/Aprovado
    list_display = ('get_aluno', 'turma', 'status', 'numero_chamada')
    list_filter = ('turma', 'status', 'turma__ano_letivo')
    search_fields = ('aluno__nome_completo',)
    autocomplete_fields = ['aluno', 'turma']

    def get_aluno(self, obj):
        return obj.aluno.nome_completo
    get_aluno.short_description = 'Aluno'

@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ('nome', )
    search_fields = ('nome',)

@admin.register(Avaliacao)
class AvaliacaoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'disciplina', 'turma', 'data_aplicacao')
    list_filter = ('turma', 'disciplina', 'data_aplicacao')
    search_fields = ('titulo',)

@admin.register(Resultado)
class ResultadoAdmin(admin.ModelAdmin):
    # CORREÇÃO: 'aluno' virou um método que busca através da matrícula
    list_display = ('get_aluno', 'get_turma', 'avaliacao', 'acertos', 'percentual', 'status')
    list_filter = ('status', 'avaliacao__disciplina')
    readonly_fields = ('percentual', 'status')

    def get_aluno(self, obj):
        return obj.matricula.aluno.nome_completo
    get_aluno.short_description = 'Aluno'

    def get_turma(self, obj):
        return obj.matricula.turma.nome
    get_turma.short_description = 'Turma'

@admin.register(Questao)
class QuestaoAdmin(admin.ModelAdmin):
    list_display = ('enunciado_curto', 'disciplina', 'dificuldade', 'get_descritor_codigo')
    list_filter = ('disciplina', 'dificuldade', 'serie')
    search_fields = ('enunciado', 'descritor__codigo') 
    
    def enunciado_curto(self, obj):
        return obj.enunciado[:50] + "..."
    
    def get_descritor_codigo(self, obj):
        return obj.descritor.codigo if obj.descritor else "-"
    get_descritor_codigo.short_description = 'Descritor'

@admin.register(RespostaDetalhada)
class RespostaDetalhadaAdmin(admin.ModelAdmin):
    list_display = ('get_aluno', 'get_prova', 'get_descritor', 'acertou')
    list_filter = ('acertou', 'questao__descritor', 'resultado__avaliacao__turma')
    
    def get_aluno(self, obj):
        # CORREÇÃO: Caminho atualizado (Resultado -> Matrícula -> Aluno)
        return obj.resultado.matricula.aluno.nome_completo
    get_aluno.short_description = 'Aluno' 
    
    def get_prova(self, obj):
        return obj.resultado.avaliacao.titulo
    get_prova.short_description = 'Avaliação'

    def get_descritor(self, obj):
        if obj.questao and obj.questao.descritor:
            return obj.questao.descritor.codigo
        return "-"
    get_descritor.short_description = 'Descritor'

@admin.register(ConfiguracaoSistema)
class ConfiguracaoSistemaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        if ConfiguracaoSistema.objects.exists():
            return False
        return True
    def has_delete_permission(self, request, obj=None):
        return False

# --- GESTÃO PEDAGÓGICA ---

@admin.register(NDI)
class NDIAdmin(admin.ModelAdmin):
    # CORREÇÃO: Exibição ajustada para Matrícula
    list_display = ('get_aluno', 'bimestre', 'ndi_final')
    list_filter = ('bimestre', 'matricula__turma')

    def get_aluno(self, obj):
        return obj.matricula.aluno.nome_completo
    get_aluno.short_description = 'Aluno'

@admin.register(PlanoEnsino)
class PlanoEnsinoAdmin(admin.ModelAdmin):
    list_display = ('disciplina_nome', 'turma', 'ano_letivo')

# --- REGISTROS SIMPLES ---

admin.site.register(ItemGabarito)
admin.site.register(Descritor)
admin.site.register(TopicoPlano)

# --- AJUDA ---

@admin.register(CategoriaAjuda)
class CategoriaAjudaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'icone')

@admin.register(Tutorial)
class TutorialAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'publico', 'data_criacao')
    list_filter = ('publico', 'categoria')
    search_fields = ('titulo', 'descricao')