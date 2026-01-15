from django.contrib import admin
from .models import Turma, Aluno, Disciplina, Avaliacao, Resultado, Questao, RespostaDetalhada

@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ano_letivo')

@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    list_display = ('nome_completo', 'turma')
    list_filter = ('turma',)

@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ('nome', )

@admin.register(Avaliacao)
class AvaliacaoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'disciplina', 'turma', 'data_aplicacao')
    list_filter = ('turma', 'disciplina')

@admin.register(Resultado)
class ResultadoAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'avaliacao', 'acertos', 'percentual', 'status')
    list_filter = ('status', 'avaliacao__turma', 'avaliacao__disciplina')
    readonly_fields = ('percentual', 'status')

@admin.register(Questao)
class QuestaoAdmin(admin.ModelAdmin):
    # CORREÇÃO AQUI: Trocamos 'habilidade_bncc' por 'descritor'
    list_display = ('enunciado_curto', 'disciplina', 'dificuldade', 'descritor')
    list_filter = ('disciplina', 'dificuldade')
    search_fields = ('enunciado', 'descritor')
    
    def enunciado_curto(self, obj):
        return obj.enunciado[:50] + "..."

@admin.register(RespostaDetalhada)
class RespostaDetalhadaAdmin(admin.ModelAdmin):
    list_display = ('get_aluno', 'get_prova', 'get_descritor', 'acertou')
    list_filter = ('acertou', 'questao__descritor', 'resultado__avaliacao__turma')
    
    # Truques para mostrar campos de outras tabelas
    def get_aluno(self, obj):
        return obj.resultado.aluno.nome_completo
    get_aluno.short_description = 'Aluno'
    
    def get_prova(self, obj):
        return obj.resultado.avaliacao.titulo
    get_prova.short_description = 'Avaliação'

    def get_descritor(self, obj):
        return obj.questao.get_descritor_display()
    get_descritor.short_description = 'Descritor'