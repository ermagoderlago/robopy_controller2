
import os
import logging
from pathlib import Path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TechnicalDocumentProcessor:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.pdf_dir = self.base_dir / "pdf"
        self.md_dir = self.base_dir / "MD"
        self.md_dir.mkdir(parents=True, exist_ok=True)
        
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = True
        
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

    def process_all(self):
        files_pdf = list(self.pdf_dir.glob("*.pdf"))
        if not files_pdf:
            logger.info(f"Nessun file PDF trovato in: {self.pdf_dir}")
            return []

        results = []
        for pdf_path in files_pdf:
            try:
                logger.info(f"Processando: {pdf_path.name}")
                result = self.converter.convert(pdf_path)
                markdown_content = result.document.export_to_markdown()
                
                output_md = self.md_dir / f"{pdf_path.stem}.md"
                with open(output_md, "w", encoding="utf-8") as f:
                    f.write(markdown_content)
                    
                logger.info(f"✅ Convertito: {output_md.name}")
                results.append(str(output_md))
            except Exception as e:
                logger.error(f"❌ Errore su {pdf_path.name}: {e}")
        
        return results

if __name__ == "__main__":
    # Test path based on workspace
    base_path = "/home/robopy/severus_ws/istruzioni_tecniche"
    processor = TechnicalDocumentProcessor(base_path)
    generated = processor.process_all()
    print(f"Generati {len(generated)} file markdown.")
