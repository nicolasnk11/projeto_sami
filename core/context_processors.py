from .models import ConfiguracaoSistema
from django.db.utils import OperationalError, ProgrammingError

def configuracao_escola(request):
    config = None
    try:
        # Tenta pegar a configuração do banco
        config = ConfiguracaoSistema.objects.first()
    except (OperationalError, ProgrammingError):
        # Se a tabela não existir ainda (durante migrações ou reset), ignora
        config = None

    # Se não tiver config ou deu erro, cria um dicionário padrão na memória
    if not config:
        config = {
            'nome_escola': 'SAMI System',
            'cor_primaria': '#0f172a',
            'cor_destaque': '#3b82f6',
            'logo': None
        }
    
    return {'escola_config': config}