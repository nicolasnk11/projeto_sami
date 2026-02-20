from django import forms
from django.utils import timezone
from .models import Avaliacao, Resultado, Turma, Questao, Disciplina, Aluno, Matricula

# ==============================================================================
# FORMULÁRIOS DE AVALIAÇÃO E RESULTADO
# ==============================================================================

class ResultadoForm(forms.ModelForm):
    class Meta:
        model = Resultado
        fields = ['avaliacao', 'matricula', 'acertos', 'total_questoes']
        labels = {
            'matricula': 'Aluno (Matrícula Ativa)'
        }
        widgets = {
            'avaliacao': forms.Select(attrs={'class': 'form-select'}),
            'matricula': forms.Select(attrs={'class': 'form-select'}),
            'acertos': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Ex: 7'}),
            'total_questoes': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Ex: 10'}),
        }
    
    # 🔥 MELHORIA: Filtra apenas as avaliações e matrículas do ano atual
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ano_atual = timezone.now().year
        self.fields['avaliacao'].queryset = Avaliacao.objects.filter(turma__ano_letivo=ano_atual).order_by('-data_aplicacao')
        self.fields['matricula'].queryset = Matricula.objects.filter(turma__ano_letivo=ano_atual, status='CURSANDO').order_by('aluno__nome_completo')

class AvaliacaoForm(forms.ModelForm):
    class Meta:
        model = Avaliacao
        fields = ['titulo', 'data_aplicacao', 'disciplina', 'turma']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control'}),
            'data_aplicacao': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'disciplina': forms.Select(attrs={'class': 'form-select'}),
            'turma': forms.Select(attrs={'class': 'form-select'}),
        }
    
    # 🔥 MELHORIA: Só mostra Turmas do ano atual (Ex: 2026) na hora de criar prova
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ano_atual = timezone.now().year
        self.fields['turma'].queryset = Turma.objects.filter(ano_letivo=ano_atual).order_by('nome')
        self.fields['disciplina'].queryset = Disciplina.objects.all().order_by('nome')

class DefinirGabaritoForm(forms.ModelForm):
    class Meta:
        model = Avaliacao
        fields = ['questoes']
        widgets = {
            'questoes': forms.CheckboxSelectMultiple()
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['questoes'].queryset = Questao.objects.filter(disciplina=self.instance.disciplina)

# ==============================================================================
# FORMULÁRIOS DE CADASTRO E IMPORTAÇÃO
# ==============================================================================

class AlunoForm(forms.ModelForm):
    class Meta:
        model = Aluno
        fields = ['nome_completo', 'cpf', 'data_nascimento', 'foto']
        widgets = {
            'nome_completo': forms.TextInput(attrs={'class': 'form-control'}),
            'cpf': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '000.000.000-00'}),
            'data_nascimento': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'foto': forms.FileInput(attrs={'class': 'form-control'}),
        }

class ImportarAlunosForm(forms.Form):
    arquivo_excel = forms.FileField(
        label="Selecione a planilha de Alunos (.xlsx)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx, .csv'})
    )

class GerarProvaForm(forms.Form):
    disciplina = forms.ModelChoiceField(
        queryset=Disciplina.objects.all().order_by('nome'), # 🔥 Adicionado order_by para o select ficar alfabético
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Escolha a Matéria"
    )
    quantidade = forms.IntegerField(
        min_value=1, 
        max_value=50, 
        initial=5,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label="Quantas questões?"
    )

class ImportarQuestoesForm(forms.Form):
    arquivo_excel = forms.FileField(
        label="Selecione o arquivo Excel (.xlsx)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx'})
    )