from django import forms
from .models import Avaliacao, Resultado, Turma, Questao, Disciplina, Aluno

class ResultadoForm(forms.ModelForm):
    class Meta:
        model = Resultado
        fields = ['avaliacao', 'aluno', 'acertos', 'total_questoes']
        widgets = {
            'avaliacao': forms.Select(attrs={'class': 'form-select'}),
            'aluno': forms.Select(attrs={'class': 'form-select'}),
            'acertos': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Ex: 7'}),
            'total_questoes': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Ex: 10'}),
        }

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

class GerarProvaForm(forms.Form):
    disciplina = forms.ModelChoiceField(
        queryset=Disciplina.objects.all(),
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

# CORREÇÃO AQUI: Esta classe deve ficar encostada na margem esquerda (fora da anterior)
class ImportarQuestoesForm(forms.Form):
    # CORREÇÃO AQUI: O campo deve estar recuado (para dentro da classe)
    arquivo_excel = forms.FileField(
        label="Selecione o arquivo Excel (.xlsx)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx'})
    )


class DefinirGabaritoForm(forms.ModelForm):
    class Meta:
        model = Avaliacao
        fields = ['questoes']
        widgets = {
            'questoes': forms.CheckboxSelectMultiple() # Cria várias caixinhas para marcar
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtra para mostrar só questões da mesma matéria da prova
        if self.instance and self.instance.pk:
            self.fields['questoes'].queryset = Questao.objects.filter(disciplina=self.instance.disciplina)

class ImportarAlunosForm(forms.Form):
    arquivo_excel = forms.FileField(label="Selecione a planilha de Alunos (.xlsx)")


# Em core/forms.py
class AlunoForm(forms.ModelForm):
    class Meta:
        model = Aluno
        fields = ['nome_completo', 'turma']