# core/context_processors.py
from .models import ConfiguracaoSistema

def dados_escola(request):
    # Pega a config ou cria uma padrão se não existir
    config = ConfiguracaoSistema.objects.first()
    if not config:
        config = ConfiguracaoSistema.objects.create(
            nome_escola="Escola Padrão SAMI",
            cor_primaria="#1e293b",
            cor_secundaria="#3b82f6"
        )
    return {'escola': config}