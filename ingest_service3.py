import boto3
import pandas as pd
import json
import os
import logging
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

# Configurar el logging
log_directory = "/home/ubuntu/logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(f"{log_directory}/ingest_service3.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Cargar las variables de entorno desde el archivo .env
load_dotenv()

def create_boto3_session():
    """Crea una sesión de boto3 usando las credenciales especificadas en el archivo de configuración."""
    try:
        session = boto3.Session(region_name=os.getenv('AWS_REGION', 'us-east-1'))
        return session
    except (ClientError, NoCredentialsError) as e:
        logger.error(f"Error al crear la sesión de boto3: {e}")
        raise

def scan_dynamodb_table(session, table_name):
    """Realiza un scan de una tabla DynamoDB con paginación."""
    dynamodb = session.client('dynamodb')
    paginator = dynamodb.get_paginator('scan')
    response_iterator = paginator.paginate(TableName=table_name)
    
    items = []
    for page in response_iterator:
        items.extend(page['Items'])
    
    return items

def process_dynamodb_items(items):
    """Procesa los elementos DynamoDB y los transforma a un formato listo para CSV."""
    processed_items = []

    for item in items:
        flat_item = {}
        for key, value in item.items():
            for data_type, data_value in value.items():
                if data_type == 'S':
                    flat_item[key] = data_value
                elif data_type == 'N':
                    flat_item[key] = float(data_value)
                elif data_type == 'BOOL':
                    flat_item[key] = data_value
                elif data_type == 'M':
                    # Aplanar el diccionario anidado
                    for sub_key, sub_value in data_value.items():
                        flat_item[f"{key}_{sub_key}"] = sub_value.get('S', str(sub_value))
                elif data_type == 'L':
                    # Convertir listas a cadenas JSON limpias
                    list_values = [v.get('S', str(v)) for v in data_value]
                    flat_item[key] = json.dumps(list_values)
                else:
                    flat_item[key] = str(data_value)
        processed_items.append(flat_item)
    
    return processed_items

def save_to_csv(data, file_name):
    """Guarda los datos en un archivo CSV."""
    df = pd.DataFrame(data)
    df.to_csv(file_name, index=False)
    logger.info(f"Archivo CSV guardado: {file_name}")

def main():
    table_name = os.getenv('DYNAMODB_TABLE_3_PROD')
    output_file = os.getenv('OUTPUT_FILE', 'output.csv')
    
    if not table_name:
        logger.error("Error: DYNAMODB_TABLE_3_PROD es obligatorio.")
        return
    
    session = create_boto3_session()
    
    logger.info(f"Escaneando la tabla DynamoDB: {table_name}...")
    items = scan_dynamodb_table(session, table_name)
    
    logger.info("Procesando los elementos de DynamoDB...")
    processed_items = process_dynamodb_items(items)
    
    logger.info(f"Guardando los datos procesados en el archivo CSV: {output_file}...")
    save_to_csv(processed_items, output_file)
    
    logger.info("Proceso completado con éxito.")

if __name__ == "__main__":
    main()