from django import forms
from django.utils import timezone
from .models import Avaliacao, Resultado, Turma, Questao, Disciplina, Aluno, Matricula, Professor, Alocacao 

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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ano_atual = timezone.now().year
        self.fields['avaliacao'].queryset = Avaliacao.objects.filter(alocacao__turma__ano_letivo=ano_atual).order_by('-data_aplicacao')
        self.fields['matricula'].queryset = Matricula.objects.filter(turma__ano_letivo=ano_atual, status='CURSANDO').order_by('aluno__nome_completo')

class AvaliacaoForm(forms.ModelForm):
    class Meta:
        model = Avaliacao
        fields = ['titulo', 'data_aplicacao', 'alocacao']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control'}),
            'data_aplicacao': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'alocacao': forms.Select(attrs={'class': 'form-select'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ano_atual = timezone.now().year
        self.fields['alocacao'].queryset = Alocacao.objects.filter(turma__ano_letivo=ano_atual).order_by('turma__nome', 'disciplina__nome')

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
            self.fields['questoes'].queryset = Questao.objects.filter(disciplina=self.instance.alocacao.disciplina)

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
        queryset=Disciplina.objects.all().order_by('nome'),
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

class ProfessorCadastroForm(forms.ModelForm):
    nome_completo = forms.CharField(
        max_length=150, 
        required=True, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: João Carlos da Silva'})
    )
    email = forms.EmailField(
        required=False, 
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'joao@escola.com (Opcional)'})
    )
    disciplinas = forms.ModelMultipleChoiceField(
        queryset=Disciplina.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '4'}),
        required=True,
        help_text="Segure CTRL para selecionar mais de uma."
    )
    turmas = forms.ModelMultipleChoiceField(
        queryset=Turma.objects.none(),
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '6'}),
        required=False,
        help_text="Opcional. Segure CTRL para selecionar mais de uma."
    )

    class Meta:
        model = Professor
        fields = ['nome_completo'] 

    def __init__(self, ano_atual, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['turmas'].queryset = Turma.objects.filter(ano_letivo=ano_atual).order_by('nome')