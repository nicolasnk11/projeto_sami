from django.db import models

# ==============================================================================
# 1. ESTRUTURA BASE (Blindada)
# ==============================================================================

class Disciplina(models.Model):
    nome = models.CharField(max_length=50, unique=True, verbose_name="Nome da Disciplina")
    
    def __str__(self):
        return self.nome

class Turma(models.Model):
    nome = models.CharField(max_length=50, help_text="Ex: 1º Ano A - T.I.")
    ano_letivo = models.IntegerField(default=2026)
    
    def __str__(self):
        return f"{self.nome} ({self.ano_letivo})"

class Aluno(models.Model):
    nome_completo = models.CharField(max_length=100)
    
    # SEGURANÇA 1: PROTECT
    # Não deixa apagar a Turma se tiver alunos nela.
    turma = models.ForeignKey(Turma, on_delete=models.PROTECT)
    
    # SOFT DELETE: Aluno transferido não é apagado, só inativado.
    ativo = models.BooleanField(default=True, verbose_name="Matrícula Ativa")
    
    def __str__(self):
        status = "" if self.ativo else " (Inativo)"
        return f"{self.nome_completo}{status}"

# ==============================================================================
# 2. PADRONIZAÇÃO DE HABILIDADES (BNCC / SPAECE)
# ==============================================================================

class Descritor(models.Model):
    """
    Tabela para guardar TODOS os descritores de todas as matérias.
    Ex: 'D12' (Matemática) ou 'EM13CHS101' (Ciências Humanas)
    """
    codigo = models.CharField(max_length=20) # Ex: D1, EM13CNT101
    descricao = models.TextField() # Ex: Identificar a localização de números...
    disciplina = models.ForeignKey(Disciplina, on_delete=models.CASCADE)
    
    # Tema ou Unidade Temática (Opcional, mas bom para organizar)
    tema = models.CharField(max_length=100, null=True, blank=True, help_text="Ex: Geometria, Termodinâmica")

    def __str__(self):
        return f"{self.codigo} - {self.descricao[:50]}..."

    class Meta:
        verbose_name = "Descritor / Habilidade"
        verbose_name_plural = "Descritores"
        ordering = ['disciplina', 'codigo']

# ==============================================================================
# 3. BANCO DE QUESTÕES
# ==============================================================================

class Questao(models.Model):

    SERIE_CHOICES = [
        (1, '1º Ano'),
        (2, '2º Ano'),
        (3, '3º Ano'),
    ]
    serie = models.IntegerField(choices=SERIE_CHOICES, default=3, verbose_name="Série Alvo")  
    
    DIFICULDADE_CHOICES = [
        ('F', 'Fácil'),
        ('M', 'Média'),
        ('D', 'Difícil'),
    ]
    
    OPCOES_GABARITO = [
        ('A', 'Letra A'),
        ('B', 'Letra B'),
        ('C', 'Letra C'),
        ('D', 'Letra D'),
        ('E', 'Letra E'), # Adicionado E para Ensino Médio
    ]

    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    
    # VINCULO DINÂMICO: Pega da tabela Descritor, não mais hardcoded
    descritor = models.ForeignKey(Descritor, on_delete=models.SET_NULL, null=True, verbose_name="Habilidade Associada")
    imagem = models.ImageField(upload_to='questoes_imgs/', blank=True, null=True)
    enunciado = models.TextField()
    imagem = models.ImageField(upload_to='questoes/', null=True, blank=True, verbose_name="Imagem de Apoio")
    dificuldade = models.CharField(max_length=1, choices=DIFICULDADE_CHOICES, default='M')

    alternativa_a = models.CharField(max_length=500)
    alternativa_b = models.CharField(max_length=500)
    alternativa_c = models.CharField(max_length=500)
    alternativa_d = models.CharField(max_length=500)
    alternativa_e = models.CharField(max_length=500, blank=True, null=True) # Opcional
    
    gabarito = models.CharField(max_length=1, choices=OPCOES_GABARITO)

    def __str__(self):
        cod = self.descritor.codigo if self.descritor else "Geral"
        return f"[{self.disciplina.nome}] {cod} - {self.enunciado[:40]}..."
    
    class Meta:
        verbose_name = "Questão"
        verbose_name_plural = "Banco de Questões"

# ==============================================================================
# 4. AVALIAÇÃO E RESULTADOS
# ==============================================================================

class Avaliacao(models.Model):
    titulo = models.CharField(max_length=100, help_text="Ex: Prova Global - 1º Bimestre")
    data_aplicacao = models.DateField()
    
    # SEGURANÇA 2: PROTECT
    # Não apaga a disciplina se tiver provas cadastradas
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT)
    turma = models.ForeignKey(Turma, on_delete=models.PROTECT)
    aluno = models.ForeignKey(Aluno, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Vinculo com Banco de Questões (Opcional, pois pode ser prova manual)
    questoes = models.ManyToManyField(Questao, related_name='avaliacoes', blank=True)

    def __str__(self):
        return f"{self.titulo} - {self.turma}"

    class Meta:
        verbose_name = "Avaliação"
        verbose_name_plural = "Avaliações"

class ItemGabarito(models.Model):
    """
    Define o gabarito de CADA questão da prova (seja do banco ou manual)
    """
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name='itens_gabarito')
    numero = models.IntegerField()
    
    # SEGURANÇA 3: SET_NULL
    # Se apagar a questão do banco, o item continua existindo na prova (sem link)
    questao_banco = models.ForeignKey(Questao, on_delete=models.SET_NULL, null=True, blank=True)
    
    resposta_correta = models.CharField(max_length=1) 
    
    # Se for manual, o professor seleciona o descritor aqui
    descritor = models.ForeignKey(Descritor, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['numero']
        unique_together = ['avaliacao', 'numero']

    def __str__(self):
        return f"[{self.avaliacao}] Q{self.numero} - Gab: {self.resposta_correta}"

class Resultado(models.Model):
    STATUS_CHOICES = [
        ('ADQ', 'Adequado (75-100%)'),
        ('INT', 'Intermediário (50-74%)'),
        ('CRI', 'Crítico (25-49%)'),
        ('MCR', 'Muito Crítico (0-24%)'),
    ]

    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE)
    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE)
    acertos = models.IntegerField()
    total_questoes = models.IntegerField()
    
    percentual = models.FloatField(editable=False, null=True, blank=True)
    status = models.CharField(max_length=3, choices=STATUS_CHOICES, editable=False, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.total_questoes > 0:
            self.percentual = (self.acertos / self.total_questoes) * 100
        else:
            self.percentual = 0

        if self.percentual >= 75: self.status = 'ADQ'
        elif self.percentual >= 50: self.status = 'INT'
        elif self.percentual >= 25: self.status = 'CRI'
        else: self.status = 'MCR'
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.aluno} - {self.percentual}%"

class RespostaDetalhada(models.Model):
    resultado = models.ForeignKey(Resultado, on_delete=models.CASCADE, related_name='respostas_detalhadas')
    
    # SEGURANÇA 4: SET_NULL
    # O histórico do que o aluno errou fica salvo, mesmo se a questão for deletada
    questao = models.ForeignKey(Questao, on_delete=models.SET_NULL, null=True, blank=True)
    item_gabarito = models.ForeignKey(ItemGabarito, on_delete=models.SET_NULL, null=True, blank=True)
    
    acertou = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.resultado.aluno} - {self.acertou}"

# Em core/models.py

class ConfiguracaoSistema(models.Model):
    nome_escola = models.CharField(max_length=100, default="SAMI Escolar")
    cor_primaria = models.CharField(max_length=7, default="#0f172a", help_text="Cor Hexadecimal (Ex: #0f172a)")
    cor_secundaria = models.CharField(max_length=7, default="#3b82f6", help_text="Cor Hexadecimal (Ex: #3b82f6)")
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    endereco = models.CharField(max_length=200, blank=True, null=True)
    
    def __str__(self):
        return "Configuração Visual da Escola"

    def save(self, *args, **kwargs):
        # Garante que só exista 1 configuração no banco
        if not self.pk and ConfiguracaoSistema.objects.exists():
            return
        super(ConfiguracaoSistema, self).save(*args, **kwargs)

        
class NDI(models.Model):
    BIMESTRES = [
        (1, '1º Bimestre'), (2, '2º Bimestre'),
        (3, '3º Bimestre'), (4, '4º Bimestre'),
    ]

    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE)
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE) # Redundante mas ajuda na filtragem
    bimestre = models.IntegerField(choices=BIMESTRES, default=1)
    
    # Notas Qualitativas (0-10)
    nota_frequencia = models.FloatField(default=0)
    nota_atividade = models.FloatField(default=0)
    nota_comportamento = models.FloatField(default=0)
    
    # Notas Quantitativas (0-10)
    nota_prova_parcial = models.FloatField(default=0)
    nota_prova_bimestral = models.FloatField(default=0)

    class Meta:
        unique_together = ('aluno', 'bimestre') # Um aluno só pode ter uma NDI por bimestre

    def __str__(self):
        return f"NDI {self.aluno.nome_completo} - {self.bimestre}º Bim"

    @property
    def ndi_parcial(self):
        return (self.nota_frequencia + self.nota_atividade + self.nota_comportamento) / 3

    @property
    def ndi_final(self):
        return (self.ndi_parcial + self.nota_prova_parcial + self.nota_prova_bimestral) / 3

class PlanoEnsino(models.Model):
    turma = models.ForeignKey(Turma, on_delete=models.CASCADE)
    disciplina_nome = models.CharField(max_length=100)
    ano_letivo = models.IntegerField(default=2026)
    criado_em = models.DateTimeField(auto_now_add=True)
    
    # Campo para o arquivo (PDF/Word/Excel)
    arquivo = models.FileField(upload_to='planos_ensino/', blank=True, null=True)

    class Meta:
        unique_together = ('turma', 'disciplina_nome', 'ano_letivo')

    def __str__(self):
        return f"Plano {self.disciplina_nome} - {self.turma}"

    # ... dentro da class PlanoEnsino ...

    def progresso(self):
        total = self.topicos.count()
        if total == 0: return 0
        
        # CORREÇÃO AQUI: Agora filtramos por status='DONE' em vez de concluido=True
        concluidos = self.topicos.filter(status='DONE').count()
        
        return int((concluidos / total) * 100)

    @property
    def icone_arquivo(self):
        if not self.arquivo: return 'bi-file-earmark'
        try:
            ext = os.path.splitext(self.arquivo.name)[1].lower()
        except:
            return 'bi-file-earmark'
            
        if ext in ['.pdf']: return 'bi-file-earmark-pdf text-danger'
        if ext in ['.doc', '.docx']: return 'bi-file-earmark-word text-primary'
        if ext in ['.xls', '.xlsx']: return 'bi-file-earmark-excel text-success'
        if ext in ['.jpg', '.png', '.jpeg']: return 'bi-file-earmark-image text-warning'
        return 'bi-file-earmark-text text-secondary'

class TopicoPlano(models.Model):
    BIMESTRES = [
        (1, '1º Bimestre'), (2, '2º Bimestre'),
        (3, '3º Bimestre'), (4, '4º Bimestre'),
    ]
    
    STATUS_CHOICES = [
        ('TODO', 'A Planejar'),
        ('DOING', 'Em Aula'),
        ('DONE', 'Concluído'),
    ]
    
    plano = models.ForeignKey(PlanoEnsino, related_name='topicos', on_delete=models.CASCADE)
    bimestre = models.IntegerField(choices=BIMESTRES)
    conteudo = models.CharField(max_length=255)
    # Novo campo de Status
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='TODO')
    # Data opcional para controle
    data_prevista = models.DateField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.conteudo} ({self.get_status_display()})"