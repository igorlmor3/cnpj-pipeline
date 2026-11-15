"""Agente de perguntas em linguagem natural sobre a gold (texto para SQL).

A parte critica de seguranca esta em sql_guard.py: nenhum SQL gerado por modelo de linguagem chega
ao Athena sem passar por ela.
"""
