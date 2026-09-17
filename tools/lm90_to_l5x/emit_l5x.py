from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape

from .model import Conversion, TagDef

SOFTWARE_REV = "32.11"
MAJOR = "32"
MINOR = "11"
PRODUCT_CODE = "164"  # 1756-L81E


def _cdata(text: str) -> str:
    text = (text or "").replace("]]>", "]] >")
    return f"<![CDATA[{text}]]>"


def _desc(text: str) -> str:
    if not text:
        return ""
    return f"<Description>\n{_cdata(text)}\n</Description>\n"


def _base_tag_xml(tag: TagDef) -> str:
    desc = _desc(tag.description)
    if tag.datatype == "BOOL":
        data = """<Data Format="L5K">
<![CDATA[0]]>
</Data>
<Data Format="Decorated">
<DataValue DataType="BOOL" Radix="Decimal" Value="0"/>
</Data>
"""
        extra = ' Radix="Decimal"'
    elif tag.datatype == "INT":
        data = """<Data Format="L5K">
<![CDATA[0]]>
</Data>
<Data Format="Decorated">
<DataValue DataType="INT" Radix="Decimal" Value="0"/>
</Data>
"""
        extra = ' Radix="Decimal"'
    elif tag.datatype == "DINT":
        data = """<Data Format="L5K">
<![CDATA[0]]>
</Data>
<Data Format="Decorated">
<DataValue DataType="DINT" Radix="Decimal" Value="0"/>
</Data>
"""
        extra = ' Radix="Decimal"'
    elif tag.datatype == "TIMER":
        data = """<Data Format="L5K">
<![CDATA[[0,0,0]]]>
</Data>
<Data Format="Decorated">
<Structure DataType="TIMER">
<DataValueMember Name="PRE" DataType="DINT" Radix="Decimal" Value="0"/>
<DataValueMember Name="ACC" DataType="DINT" Radix="Decimal" Value="0"/>
<DataValueMember Name="EN" DataType="BOOL" Value="0"/>
<DataValueMember Name="TT" DataType="BOOL" Value="0"/>
<DataValueMember Name="DN" DataType="BOOL" Value="0"/>
</Structure>
</Data>
"""
        extra = ""
    elif tag.datatype == "COUNTER":
        data = """<Data Format="L5K">
<![CDATA[[0,0,0]]]>
</Data>
<Data Format="Decorated">
<Structure DataType="COUNTER">
<DataValueMember Name="PRE" DataType="DINT" Radix="Decimal" Value="0"/>
<DataValueMember Name="ACC" DataType="DINT" Radix="Decimal" Value="0"/>
<DataValueMember Name="CU" DataType="BOOL" Value="0"/>
<DataValueMember Name="CD" DataType="BOOL" Value="0"/>
<DataValueMember Name="DN" DataType="BOOL" Value="0"/>
<DataValueMember Name="OV" DataType="BOOL" Value="0"/>
<DataValueMember Name="UN" DataType="BOOL" Value="0"/>
</Structure>
</Data>
"""
        extra = ""
    else:
        data = """<Data Format="L5K">
<![CDATA[0]]>
</Data>
"""
        extra = ' Radix="Decimal"'
    return (
        f'<Tag Name="{escape(tag.name)}" TagType="Base" DataType="{tag.datatype}"'
        f'{extra} Constant="false" ExternalAccess="Read/Write">\n'
        f"{desc}{data}</Tag>\n"
    )


def _alias_xml(tag: TagDef) -> str:
    desc = _desc(tag.description)
    return (
        f'<Tag Name="{escape(tag.name)}" TagType="Alias" Radix="Decimal" '
        f'AliasFor="{escape(tag.alias_for or "")}" ExternalAccess="Read/Write">\n'
        f"{desc}</Tag>\n"
    )


DEFAULT_TITLE = (
    "USS Fairfield Dual Line Temper Mill — GE Series 90-30 program TEMPER imported for "
    "1756-L81E. Base tags are GE references without %. Nicknames are aliases. Logic uses "
    "the alias when one exists."
)


def emit_l5x(
    conv: Conversion,
    controller: str = "TEMPER",
    program: str | None = None,
    title: str | None = None,
) -> str:
    program = program or controller
    title = title or (
        DEFAULT_TITLE
        if controller == "TEMPER"
        else (
            f"GE Logicmaster 90-30 program {program} imported for 1756-L81E. "
            "Base tags are GE references without %. Nicknames are aliases. "
            "Logic uses the alias when one exists."
        )
    )
    now = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    tags_xml = []
    for name in sorted(conv.tags):
        tags_xml.append(_base_tag_xml(conv.tags[name]))
    for name in sorted(conv.aliases):
        tags_xml.append(_alias_xml(conv.aliases[name]))

    routines = []
    for block in conv.blocks:
        rungs = []
        for i, rung in enumerate(block.rungs):
            comment = _desc(rung.comment)
            text = rung.text.strip()
            if not text.endswith(";"):
                text += ";"
            rungs.append(
                f'<Rung Number="{i}" Type="N">\n{comment}'
                f"<Text>\n{_cdata(text)}\n</Text>\n</Rung>\n"
            )
        if not rungs:
            rungs.append('<Rung Number="0" Type="N">\n<Text>\n<![CDATA[NOP();]]>\n</Text>\n</Rung>\n')
        routines.append(
            f'<Routine Name="{escape(block.name)}" Type="RLL">\n'
            f"{_desc(block.description)}"
            f"<RLLContent>\n{''.join(rungs)}</RLLContent>\n</Routine>\n"
        )

    program_xml = (
        f'<Program Name="{escape(program)}" TestEdits="false" MainRoutineName="MainRoutine" '
        'Disabled="false" UseAsFolder="false">\n'
        f"{_desc(f'GE Logicmaster 90-30 program {program} converted for ControlLogix 1756-L81E')}"
        "<Tags/>\n<Routines>\n"
        f"{''.join(routines)}</Routines>\n</Program>\n"
    )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="{SOFTWARE_REV}" TargetName="{escape(controller)}" TargetType="Controller" ContainsContext="false" Owner="DL-TM-PLC conversion" ExportDate="{now}" ExportOptions="NoRawData L5KData DecoratedData ForceProtectedEncoding AllProjDocTrans">
<Controller Use="Target" Name="{escape(controller)}" ProcessorType="1756-L81E" MajorRev="{MAJOR}" MinorRev="{MINOR}" ProjectCreationDate="{now}" LastModifiedDate="{now}" SFCExecutionControl="CurrentActive" SFCRestartPosition="MostRecent" SFCLastScan="DontScan" ProjectSN="16#0000_0000" MatchProjectToController="false" CanUseRPIFromProducer="false" InhibitAutomaticFirmwareUpdate="0" PassThroughConfiguration="EnabledWithAppend" DownloadProjectDocumentationAndExtendedProperties="true" DownloadProjectCustomProperties="true" ReportMinorOverflow="false">
<Description>
{_cdata(title)}
</Description>
<RedundancyInfo Enabled="false" KeepTestEditsOnSwitchOver="false"/>
<Security Code="0" ChangesToDetect="16#ffff_ffff_ffff_ffff"/>
<SafetyInfo/>
<DataTypes/>
<Modules>
<Module Name="Local" CatalogNumber="1756-L81E" Vendor="1" ProductType="14" ProductCode="{PRODUCT_CODE}" Major="{MAJOR}" Minor="{MINOR}" ParentModule="Local" ParentModPortId="1" Inhibited="false" MajorFault="true">
<EKey State="Disabled"/>
<Ports>
<Port Id="1" Address="0" Type="ICP" Upstream="false">
<Bus Size="10"/>
</Port>
<Port Id="2" Type="Ethernet" Upstream="false">
<Bus/>
</Port>
</Ports>
</Module>
</Modules>
<AddOnInstructionDefinitions/>
<Tags>
{''.join(tags_xml)}</Tags>
<Programs>
{program_xml}</Programs>
<Tasks>
<Task Name="MainTask" Type="CONTINUOUS" Priority="10" Watchdog="2000" DisableUpdateOutputs="false" InhibitTask="false">
<Description>
{_cdata(f"Continuous task running converted GE {program} logic")}
</Description>
<ScheduledPrograms>
<ScheduledProgram Name="{escape(program)}"/>
</ScheduledPrograms>
</Task>
</Tasks>
<CST MasterID="0"/>
<WallClockTime LocalTimeAdjustment="0" TimeZone="0"/>
<Trends/>
<DataLogs/>
<TimeSynchronize Priority1="128" Priority2="128" PTPEnable="false"/>
<EthernetPorts>
<EthernetPort Port="1" Label="1" PortEnabled="true"/>
</EthernetPorts>
</Controller>
</RSLogix5000Content>
"""


def emit_report(conv: Conversion, controller: str = "TEMPER", program: str | None = None) -> str:
    program = program or controller
    heading = (
        "Program: TEMPER (USS Fairfield Dual Line Temper Mill)"
        if program == "TEMPER"
        else f"Program: {program} (controller {controller})"
    )
    lines = [
        "GE Logicmaster 90-30 → ControlLogix 1756-L81E conversion report",
        heading,
        "",
        f"Base tags: {len(conv.tags)}",
        f"Alias tags (GE nicknames): {len(conv.aliases)}",
        f"Routines: {len(conv.blocks)}",
        f"Mapped rungs: {conv.mapped_rungs}",
        f"Unmapped rungs (NOP with original print in comment): {conv.unmapped_rungs}",
        "",
        "Tag rules:",
        "  %I/%Q/%M/%T/%S/%SA/%SC = BOOL  named I0001, Q0001, ...",
        "  %R/%AI/%AQ = INT (16-bit) named R0001, AI0001, ...",
        "  Nickname → alias; ladder uses the alias when present.",
        "  Hyphens in nicknames (S07-01A) become underscores (S07_01A).",
        "",
        "Routines follow GE blocks. MainRoutine is _MAIN and JSRs SYSBITS first.",
        "Each rung comment starts with the original GE rung number.",
        "",
        "Unmapped / review notes:",
    ]
    seen: set[str] = set()
    for block in conv.blocks:
        for rung in block.rungs:
            if not rung.mapped or rung.notes:
                note = "; ".join(rung.notes) or "unmapped"
                key = f"  {block.name} GE {rung.ge_number}: {note}"
                if key not in seen:
                    seen.add(key)
                    lines.append(key)
    return "\n".join(lines) + "\n"
