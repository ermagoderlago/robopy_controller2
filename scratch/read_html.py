import re

file_path = r"C:\Users\lsuffia\.gemini\antigravity\brain\0ffc9726-743a-45e9-a3ff-43b764896d0a\.system_generated\steps\6440\content.md"

with open(file_path, "r", encoding="utf-8") as f:
    html = f.read()

def clean_html(text):
    # Strip scripts, styles, comments
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    
    # Strip HTML tags but keep structure
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        clean_line = re.sub(r'<[^>]+>', '', line_str)
        clean_line = clean_line.strip()
        if clean_line:
            cleaned_lines.append(clean_line)
            
    return cleaned_lines

cleaned = clean_html(html)

output_path = r"scratch/cleaned_practices.txt"
with open(output_path, "w", encoding="utf-8") as f_out:
    f_out.write(f"Total cleaned lines: {len(cleaned)}\n\n")
    f_out.write("--- CLEANED CONTENT ---\n")
    for i, line in enumerate(cleaned):
        f_out.write(f"{i+1}: {line}\n")

print(f"Extraction successful! Cleaned file saved to {output_path}")
