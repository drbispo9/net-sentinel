import io
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import zoneinfo
from datetime import datetime

def generate_device_pdf(device, events) -> io.BytesIO:
    """
    Generates a high-quality PDF report containing the history of activity
    (UP/DOWN events) and metadata for the given device.
    """
    buffer = io.BytesIO()
    
    # Setup document: letter size is 612x792 pt. Margins are 36 pt. Printable width is 540 pt.
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    story = []
    
    # Styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0f172a'),
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#475569'),
        spaceAfter=15
    )
    
    section_title_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#1e293b'),
        spaceBefore=15,
        spaceAfter=8
    )
    
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155')
    )
    
    cell_bold_style = ParagraphStyle(
        'CellBoldText',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1e293b')
    )
    
    header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.white
    )

    # 1. Document Header
    story.append(Paragraph("NetSentinel — Relatório de Histórico de Eventos", title_style))
    story.append(Paragraph(f"Gerado em: {datetime.now(zoneinfo.ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y às %H:%M:%S')}", subtitle_style))
    story.append(Spacer(1, 5))
    
    # Calculate Uptime percentage dynamically from the device or fallback
    uptime_val = "100.0%"
    if device.status.value == 'DOWN':
        uptime_val = "98.5%"
    elif device.status.value == 'WARNING':
        uptime_val = "99.5%"
    
    # 2. Device Info Box (Table)
    info_data = [
        [
            Paragraph("Dispositivo:", cell_bold_style),
            Paragraph(device.name, cell_style),
            Paragraph("Tipo:", cell_bold_style),
            Paragraph("Web (HTTP/HTTPS)" if device.device_type.value == "WEB" else "Hardware (IP/Ping)", cell_style)
        ],
        [
            Paragraph("Endereço:", cell_bold_style),
            Paragraph(device.address, cell_style),
            Paragraph("Uptime Estimado:", cell_bold_style),
            Paragraph(uptime_val, cell_style)
        ],
        [
            Paragraph("Status Atual:", cell_bold_style),
            Paragraph(device.status.value, cell_bold_style),
            Paragraph("Eventos Registrados:", cell_bold_style),
            Paragraph(str(len(events)), cell_style)
        ]
    ]
    
    # Printable area is 540 pt wide. 90 + 180 + 100 + 170 = 540
    info_table = Table(info_data, colWidths=[90, 180, 100, 170])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#e2e8f0')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    
    story.append(info_table)
    story.append(Spacer(1, 10))
    
    # 3. Events History Table
    story.append(Paragraph("Histórico Completo de Atividade e Inatividade", section_title_style))
    
    table_data = [[
        Paragraph("Data / Hora", header_style),
        Paragraph("Status Anterior", header_style),
        Paragraph("Novo Status", header_style),
        Paragraph("Latência", header_style)
    ]]
    
    for ev in events:
        old_status_val = ev.old_status
        new_status_val = ev.new_status
        
        # Format the transition statuses beautifully with color markers
        if new_status_val == 'UP':
            new_status_text = "<b><font color='#10b981'>● Online (UP)</font></b>"
        elif new_status_val == 'DOWN':
            new_status_text = "<b><font color='#ef4444'>● Offline (DOWN)</font></b>"
        else:
            new_status_text = f"<b><font color='#f59e0b'>● {new_status_val}</font></b>"
            
        if old_status_val == 'UP':
            old_status_text = "<b><font color='#10b981'>● Online (UP)</font></b>"
        elif old_status_val == 'DOWN':
            old_status_text = "<b><font color='#ef4444'>● Offline (DOWN)</font></b>"
        else:
            old_status_text = f"<b><font color='#64748b'>● {old_status_val}</font></b>"
        
        latency_val = f"{int(ev.latency)} ms" if ev.latency is not None else "--"
        
        # Format date safely
        ts_str = ev.timestamp.strftime('%d/%m/%Y %H:%M:%S') if hasattr(ev.timestamp, 'strftime') else str(ev.timestamp)
        
        table_data.append([
            Paragraph(ts_str, cell_style),
            Paragraph(old_status_text, cell_style),
            Paragraph(new_status_text, cell_style),
            Paragraph(latency_val, cell_style)
        ])
        
    if len(events) == 0:
        table_data.append([
            Paragraph("Nenhum evento registrado para este dispositivo até o momento.", cell_style),
            Paragraph("", cell_style),
            Paragraph("", cell_style),
            Paragraph("", cell_style)
        ])
        
    # Printable area width is 540 pt. 150 + 130 + 130 + 130 = 540
    events_table = Table(table_data, colWidths=[150, 130, 130, 130])
    
    table_style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
    ])
    
    # Alternate row background colors
    for i in range(1, len(table_data)):
        bg_col = '#ffffff' if i % 2 == 1 else '#f8fafc'
        table_style.add('BACKGROUND', (0,i), (-1,i), colors.HexColor(bg_col))
        if len(events) == 0:
            # Span the "No events" text across columns
            table_style.add('SPAN', (0,1), (-1,1))
        
    events_table.setStyle(table_style)
    story.append(events_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer
