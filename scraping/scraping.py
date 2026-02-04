import re
from bs4 import BeautifulSoup
import pandas as pd

file_path = 'scraping/eventos.txt'

with open(file_path, 'r', encoding='utf-8') as file:
    content = file.read()

variable_pattern = r"var calendarEvents130_1200232061 = {(.*?)};"
match = re.search(variable_pattern, content, re.DOTALL)

events = {}
if match:
    variable_content = match.group(1)
    event_pattern = r"'(.*?)':\s*'(.*?)',"
    matches = re.findall(event_pattern, variable_content)
    
    for date, html_content in matches:
        soup = BeautifulSoup(html_content, 'html.parser')
        title = soup.find('span', class_='module_event_title').get_text(strip=True)
        description = soup.find('span', class_='module_event_description').get_text(strip=True)
        events[date] = {'title': title, 'description': description}

events_df = pd.DataFrame.from_dict(events, orient='index')
events_df.reset_index(inplace=True)
events_df.rename(columns={'index': 'date'}, inplace=True)


events_df.to_csv('scraping/eventos_extraidos.csv', index=False)
