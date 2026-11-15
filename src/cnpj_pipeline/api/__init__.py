"""API publica do agente: o que roda atras da URL que o site chama.

A funcao fica na nuvem porque so ela pode guardar a chave do modelo e a credencial da AWS; o site
estatico nao guarda segredo nenhum. Tudo aqui e escrito para falhar fechado: sem parametro, sem
contador, sem razao de gasto ou com o interruptor desligado, o modelo nao e chamado.
"""
