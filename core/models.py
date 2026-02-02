import os
from django.db import models
from django.contrib.auth.models import User

# ==============================================================================
# 1. CONFIGURAÇÃO E ESTRUTURA BASE
# ==============================================================================

class ConfiguracaoSistema(models.Model):
    nome_escola = models.CharField(max_length=100, default="EEMTI PMBC")
    cor_primaria = models.CharField(max_length=7, default="#0A2619", help_text="Cor Hexadecimal (Ex: #0A2619)")
    cor_secundaria = models.CharField(max_length=7, default="#D4AF37", help_text="Cor Hexadecimal (Ex: #D4AF37)")
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    endereco = models.CharField(max_length=200, blank=True, null=True)
    
    def __str__(self): return "Configuração Visual da Escola"

    def save(self, *args, **kwargs):
        if not self.pk and ConfiguracaoSistema.objects.exists(): return
        super(ConfiguracaoSistema, self).save(*args, **kwargs)

class Disciplina(models.Model):
    nome = models.CharField(max_length=50, unique=True, verbose_name="Nome da Disciplina")
    def __str__(self): return self.nome

class Turma(models.Model):
    nome = models.CharField(max_length=50, help_text="Ex: 3º Ano B")
    ano_letivo = models.IntegerField(default=2026)
    def __str__(self): return f"{self.nome} ({self.ano_letivo})"

class Aluno(models.Model):
    """
    Representa a PESSOA. Não tem turma aqui, pois a turma muda todo ano.
    """
    nome_completo = models.CharField(max_length=100)
    data_nascimento = models.DateField(null=True, blank=True)
    cpf = models.CharField(max_length=14, unique=True, null=True, blank=True)
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    foto = models.ImageField(upload_to='alunos/', null=True, blank=True)
    
    def __str__(self): return self.nome_completo


class Professor(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professor_perfil')
    nome_completo = models.CharField(max_length=150)
    
    # Aqui está a mágica: O professor pode ter várias disciplinas e várias turmas
    disciplinas = models.ManyToManyField(Disciplina, related_name='professores')
    turmas = models.ManyToManyField(Turma, related_name='professores')

    def __str__(self):
        return f"Prof. {self.nome_completo}"


class Matricula(models.Model):
    """
    TABELA NOVA: Liga o Aluno à Turma em um Ano específico.
    """
    STATUS_CHOICES = [
        ('CURSANDO', 'Cursando'), ('APROVADO', 'Aprovado'),
        ('REPROVADO', 'Reprovado'), ('RECUPERACAO', 'Recuperação'),
        ('FORMADO', 'Formado/Concluinte'), ('TRANSFERIDO', 'Transferido'),
    ]
    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE, related_name='matriculas')
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, related_name='alunos_matriculados')
    data_matricula = models.DateField(auto_now_add=True)
    numero_chamada = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CURSANDO')

    class Meta:
        unique_together = ['aluno', 'turma']
        verbose_name = "Matrícula"

    def __str__(self):
        return f"{self.aluno.nome_completo} - {self.turma}"

# ==============================================================================
# 2. HABILIDADES E QUESTÕES (Compatível com Backup)
# ==============================================================================

class Descritor(models.Model):
    codigo = models.CharField(max_length=20)
    descricao = models.TextField()
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE)
    tema = models.CharField(max_length=100, null=True, blank=True)
    def __str__(self): return f"{self.codigo} - {self.descricao[:50]}..."
    class Meta: ordering = ['disciplina', 'codigo']

class Questao(models.Model):
    SERIE_CHOICES = [(1, '1º Ano'), (2, '2º Ano'), (3, '3º Ano')]
    serie = models.IntegerField(choices=SERIE_CHOICES, default=3)
    DIFICULDADE_CHOICES = [('F', 'Fácil'), ('M', 'Média'), ('D', 'Difícil')]
    OPCOES_GABARITO = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D'), ('E', 'E')]

    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    descritor = models.ForeignKey(Descritor, on_delete=models.SET_NULL, null=True)
    enunciado = models.TextField()
    imagem = models.ImageField(upload_to='questoes/', null=True, blank=True)
    dificuldade = models.CharField(max_length=1, choices=DIFICULDADE_CHOICES, default='M')
    alternativa_a = models.CharField(max_length=500)
    alternativa_b = models.CharField(max_length=500)
    alternativa_c = models.CharField(max_length=500)
    alternativa_d = models.CharField(max_length=500)
    alternativa_e = models.CharField(max_length=500, blank=True, null=True)
    gabarito = models.CharField(max_length=1, choices=OPCOES_GABARITO)

    def __str__(self): return f"[{self.disciplina}] {self.enunciado[:30]}..."

# ==============================================================================
# 3. AVALIAÇÃO E RESULTADOS
# ==============================================================================

# ... seus outros imports ...

class Avaliacao(models.Model):
    titulo = models.CharField(max_length=100)
    data_aplicacao = models.DateField()
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    turma = models.ForeignKey(Turma, on_delete=models.PROTECT)
    questoes = models.ManyToManyField(Questao, related_name='avaliacoes', blank=True)
    
    # --- NOVO CAMPO (O Jeito Certo) ---
    # Vincula a prova a um aluno específico (para recuperações).
    # Se for prova geral da turma, fica vazio.
    matricula = models.ForeignKey('Matricula', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.titulo} - {self.turma}"

class ItemGabarito(models.Model):
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name='itens_gabarito')
    numero = models.IntegerField()
    questao_banco = models.ForeignKey(Questao, on_delete=models.SET_NULL, null=True, blank=True)
    resposta_correta = models.CharField(max_length=1) 
    descritor = models.ForeignKey(Descritor, on_delete=models.SET_NULL, null=True, blank=True)
    class Meta: ordering = ['numero']

class Resultado(models.Model):
    STATUS_CHOICES = [('ADQ', 'Adequado'), ('INT', 'Intermediário'), ('CRI', 'Crítico'), ('MCR', 'Muito Crítico')]
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE)
    
    # IMPORTANTE: Resultado ligado à Matrícula
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name='resultados')
    
    acertos = models.IntegerField()
    total_questoes = models.IntegerField()
    percentual = models.FloatField(editable=False, null=True, blank=True)
    status = models.CharField(max_length=3, choices=STATUS_CHOICES, editable=False, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.total_questoes > 0: self.percentual = (self.acertos / self.total_questoes) * 100
        else: self.percentual = 0
        if self.percentual >= 75: self.status = 'ADQ'
        elif self.percentual >= 50: self.status = 'INT'
        elif self.percentual >= 25: self.status = 'CRI'
        else: self.status = 'MCR'
        super().save(*args, **kwargs)

class RespostaDetalhada(models.Model):
    resultado = models.ForeignKey(Resultado, on_delete=models.CASCADE, related_name='respostas_detalhadas')
    questao = models.ForeignKey(Questao, on_delete=models.SET_NULL, null=True, blank=True)
    item_gabarito = models.ForeignKey(ItemGabarito, on_delete=models.SET_NULL, null=True, blank=True)
    acertou = models.BooleanField(default=False)
    resposta_aluno = models.CharField(max_length=1, blank=True, null=True)

# ==============================================================================
# 4. NDI (Boletim)
# ==============================================================================

class NDI(models.Model):
    # IMPORTANTE: NDI ligado à Matrícula
    matricula = models.ForeignKey(Matricula, on_delete=models.CASCADE, related_name='boletins')
    bimestre = models.IntegerField(default=1)
    
    nota_frequencia = models.FloatField(default=0)
    nota_atividade = models.FloatField(default=0)
    nota_comportamento = models.FloatField(default=0)
    nota_prova_parcial = models.FloatField(default=0)
    nota_prova_bimestral = models.FloatField(default=0)
    
    class Meta: unique_together = ('matricula', 'bimestre')
    
    @property
    def ndi_final(self):
        parcial = (self.nota_frequencia + self.nota_atividade + self.nota_comportamento) / 3
        return (parcial + self.nota_prova_parcial + self.nota_prova_bimestral) / 3

# ==============================================================================
# 5. GESTÃO DE AULAS E SUPORTE
# ==============================================================================

class PlanoEnsino(models.Model):
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE)
    disciplina_nome = models.CharField(max_length=100)
    ano_letivo = models.IntegerField(default=2026)
    criado_em = models.DateTimeField(auto_now_add=True)
    arquivo = models.FileField(upload_to='planos_ensino/', blank=True, null=True)

    class Meta: unique_together = ('turma', 'disciplina_nome', 'ano_letivo')

    def progresso(self):
        total = self.topicos.count()
        if total == 0: return 0
        concluidos = self.topicos.filter(status='DONE').count()
        return int((concluidos / total) * 100)

class TopicoPlano(models.Model):
    BIMESTRES = [(1, '1º'), (2, '2º'), (3, '3º'), (4, '4º')]
    STATUS_CHOICES = [('TODO', 'A Planejar'), ('DOING', 'Em Aula'), ('DONE', 'Concluído')]
    
    plano = models.ForeignKey(PlanoEnsino, related_name='topicos', on_delete=models.CASCADE)
    bimestre = models.IntegerField(choices=BIMESTRES)
    conteudo = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='TODO')
    data_prevista = models.DateField(null=True, blank=True)

class CategoriaAjuda(models.Model):
    nome = models.CharField(max_length=50)
    icone = models.CharField(max_length=50, default="bi-question-circle")
    def __str__(self): return self.nome

class Tutorial(models.Model):
    # CORRIGIDO: Adicionados os campos que o Admin exige
    PUBLICO_CHOICES = [
        ('PROF', 'Professor / Gestão'),
        ('ALUNO', 'Aluno / Responsável'),
        ('TODOS', 'Todos'),
    ]
    titulo = models.CharField(max_length=200)
    descricao = models.TextField()
    categoria = models.ForeignKey(CategoriaAjuda, on_delete=models.CASCADE)
    
    # Estes campos estavam faltando e causavam o erro no Admin:
    publico = models.CharField(max_length=10, choices=PUBLICO_CHOICES, default='PROF')
    data_criacao = models.DateTimeField(auto_now_add=True)
    
    link_video = models.URLField(blank=True, null=True)
    
    def __str__(self): return self.titulo