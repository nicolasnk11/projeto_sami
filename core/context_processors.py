# core/context_processors.py
from .models import ConfiguracaoSistema

def configuracao_escola(request):
    # Pega a primeira configuração ou cria uma padrão se não existir
    config = ConfiguracaoSistema.objects.first()
    if not config:
        config = ConfiguracaoSistema.objects.create(
            nome_escola="SAMI Escolar",
            cor_primaria="#0f172a",
            cor_secundaria="#3b82f6"
        )
    return {'escola': config}