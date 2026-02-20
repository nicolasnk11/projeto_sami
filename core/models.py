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
    # O ano letivo é crucial para a "Virada de Ano"
    ano_letivo = models.IntegerField(default=2026) 
    
    def __str__(self): return f"{self.nome} ({self.ano_letivo})"

class Aluno(models.Model):
    """
    Representa a PESSOA. Contém dados perenes (que não mudam a cada ano).
    INCLUI: Perfil Socioeconômico e Dados de Inclusão (AEE).
    """
    # --- DADOS PESSOAIS BÁSICOS ---
    nome_completo = models.CharField(max_length=100)
    data_nascimento = models.DateField(null=True, blank=True)
    cpf = models.CharField(max_length=14, unique=True, null=True, blank=True)
    
    # CORREÇÃO DE SEGURANÇA: SET_NULL impede que apagar o login apague o aluno
    usuario = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    foto = models.ImageField(upload_to='alunos/', null=True, blank=True)

    # --- PERFIL SOCIOECONÔMICO (Censo Escolar) ---
    COR_RACA_CHOICES = [
        ('BRANCA', 'Branca'),
        ('PRETA', 'Preta'),
        ('PARDA', 'Parda'),
        ('AMARELA', 'Amarela'),
        ('INDIGENA', 'Indígena'),
        ('NAO_DECLARADO', 'Não Declarado'),
    ]
    GENERO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Feminino'),
        ('NB', 'Não-Binário'),
        ('OUTRO', 'Outro'),
    ]
    RENDA_CHOICES = [
        ('BAIXA', 'Até 1 Salário Mínimo (Baixa Renda)'),
        ('MEDIA_BAIXA', '1 a 3 Salários Mínimos'),
        ('MEDIA', '3 a 6 Salários Mínimos'),
        ('ALTA', 'Acima de 6 Salários Mínimos'),
    ]
    INTERNET_CHOICES = [
        ('SEM', 'Sem acesso à internet'),
        ('MOVEL', 'Apenas dados móveis (Celular)'),
        ('FIXA', 'Banda Larga / Wi-Fi'),
    ]

    cor_raca = models.CharField(max_length=20, choices=COR_RACA_CHOICES, default='NAO_DECLARADO', verbose_name="Cor/Raça")
    genero = models.CharField(max_length=10, choices=GENERO_CHOICES, default='M', verbose_name="Gênero")
    renda_familiar = models.CharField(max_length=20, choices=RENDA_CHOICES, blank=True, null=True, verbose_name="Renda Familiar")
    tipo_acesso_internet = models.CharField(max_length=10, choices=INTERNET_CHOICES, default='FIXA', verbose_name="Acesso Digital")
    possui_computador = models.BooleanField(default=False, verbose_name="Possui Computador/Tablet?")

    # --- INCLUSÃO E AEE (Atendimento Educacional Especializado) ---
    is_pcd = models.BooleanField(default=False, verbose_name="É PcD / Inclusão?")
    
    DEFICIENCIA_CHOICES = [
        ('TEA', 'Transtorno do Espectro Autista (TEA)'),
        ('TDAH', 'TDAH (Laudo Clínico)'),
        ('DV', 'Deficiência Visual / Baixa Visão'),
        ('DA', 'Deficiência Auditiva'),
        ('DF', 'Deficiência Física/Motora'),
        ('DI', 'Deficiência Intelectual'),
        ('AH', 'Altas Habilidades / Superdotação'),
        ('OUTRA', 'Outra Necessidade Específica'),
    ]
    tipo_deficiencia = models.CharField(max_length=10, choices=DEFICIENCIA_CHOICES, blank=True, null=True, verbose_name="Tipo de Condição")
    
    # PEI: Plano de Ensino Individualizado (Arquivo PDF ou Imagem)
    arquivo_pei = models.FileField(upload_to='pei_alunos/', blank=True, null=True, verbose_name="Documento PEI/Laudo")
    observacoes_clinicas = models.TextField(blank=True, null=True, help_text="Cuidados específicos, medicação, suporte necessário.")

    def __str__(self): return self.nome_completo

    @property
    def tem_icone_inclusao(self):
        """Helper para o template saber se mostra o ícone de acessibilidade"""
        return self.is_pcd


class Professor(models.Model):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professor_perfil')
    nome_completo = models.CharField(max_length=150)
    disciplinas = models.ManyToManyField(Disciplina, related_name='professores')
    turmas = models.ManyToManyField(Turma, related_name='professores')

    def __str__(self):
        return f"Prof. {self.nome_completo}"


class Matricula(models.Model):
    """
    Liga o Aluno à Turma em um Ano específico.
    Guardamos aqui o resultado final para histórico.
    """
    STATUS_CHOICES = [
        ('CURSANDO', 'Cursando'), 
        ('APROVADO', 'Aprovado'),
        ('REPROVADO', 'Reprovado'), 
        ('RECUPERACAO', 'Em Recuperação Final'),
        ('FORMADO', 'Formado/Concluinte'), 
        ('TRANSFERIDO', 'Transferido'),
    ]
    
    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE, related_name='matriculas')
    
    # CORREÇÃO DE SEGURANÇA: PROTECT impede apagar a turma se tiver alunos
    turma = models.ForeignKey(Turma, on_delete=models.PROTECT, related_name='alunos_matriculados') 
    
    data_matricula = models.DateField(auto_now_add=True)
    numero_chamada = models.IntegerField(null=True, blank=True)
    
    # Status Atual (Mudará durante a "Virada de Ano")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CURSANDO')
    
    # Campo Novo: Para congelar a média final do ano (importante para histórico 2025 -> 2026)
    media_final = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ['aluno', 'turma']
        verbose_name = "Matrícula"

    def __str__(self):
        return f"{self.aluno.nome_completo} - {self.turma}"

# ==============================================================================
# 2. HABILIDADES E QUESTÕES
# ==============================================================================

class Descritor(models.Model):
    codigo = models.CharField(max_length=20)
    descricao = models.TextField()
    
    # CORREÇÃO DE SEGURANÇA: PROTECT impede apagar a disciplina e perder descritores
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    
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

class Avaliacao(models.Model):
    titulo = models.CharField(max_length=100)
    data_aplicacao = models.DateField()
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    turma = models.ForeignKey(Turma, on_delete=models.PROTECT)
    questoes = models.ManyToManyField(Questao, related_name='avaliacoes', blank=True)
    matricula = models.ForeignKey('Matricula', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self): return f"{self.titulo} - {self.turma}"

class ItemGabarito(models.Model):
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name='itens_gabarito')
    numero = models.IntegerField()
    questao_banco = models.ForeignKey(Questao, on_delete=models.SET_NULL, null=True, blank=True)
    resposta_correta = models.CharField(max_length=1) 
    descritor = models.ForeignKey(Descritor, on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta: ordering = ['numero']

    # NOVO STR
    def __str__(self):
        return f"Q{self.numero} - {self.avaliacao.titulo}"

class Resultado(models.Model):
    STATUS_CHOICES = [('ADQ', 'Adequado'), ('INT', 'Intermediário'), ('CRI', 'Crítico'), ('MCR', 'Muito Crítico')]
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE)
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

    # NOVO STR
    def __str__(self):
        return f"Resultado: {self.matricula.aluno.nome_completo[:15]} - {self.avaliacao.titulo[:15]}"

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

    # NOVO STR
    def __str__(self):
        return f"Boletim {self.bimestre}º Bim - {self.matricula.aluno.nome_completo[:20]}"

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

    # NOVO STR
    def __str__(self):
        return f"Plano {self.disciplina_nome} - {self.turma.nome}"

class TopicoPlano(models.Model):
    BIMESTRES = [(1, '1º'), (2, '2º'), (3, '3º'), (4, '4º')]
    STATUS_CHOICES = [('TODO', 'A Planejar'), ('DOING', 'Em Aula'), ('DONE', 'Concluído')]
    
    plano = models.ForeignKey(PlanoEnsino, related_name='topicos', on_delete=models.CASCADE)
    bimestre = models.IntegerField(choices=BIMESTRES)
    conteudo = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='TODO')
    data_prevista = models.DateField(null=True, blank=True)

    # NOVO STR
    def __str__(self):
        return f"{self.bimestre}ºB: {self.conteudo[:30]}..."

class CategoriaAjuda(models.Model):
    nome = models.CharField(max_length=50)
    icone = models.CharField(max_length=50, default="bi-question-circle")
    def __str__(self): return self.nome

class Tutorial(models.Model):
    PUBLICO_CHOICES = [
        ('PROF', 'Professor / Gestão'),
        ('ALUNO', 'Aluno / Responsável'),
        ('TODOS', 'Todos'),
    ]
    titulo = models.CharField(max_length=200)
    descricao = models.TextField()
    categoria = models.ForeignKey(CategoriaAjuda, on_delete=models.CASCADE)
    publico = models.CharField(max_length=10, choices=PUBLICO_CHOICES, default='PROF')
    data_criacao = models.DateTimeField(auto_now_add=True)
    link_video = models.URLField(blank=True, null=True)
    
    def __str__(self): return self.titulo