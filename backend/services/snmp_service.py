import asyncio
from pysnmp.hlapi.asyncio import *

async def get_snmp_cpu(ip: str, community: str, oid_str: str, version: str = 'v2c') -> float:
    """
    Realiza um SNMP GET assíncrono para coletar a OID especificada.
    Lança TimeoutError se o dispositivo não responder.
    Retorna o valor coletado como float.
    """
    mp_model = 1 if version.lower() == 'v2c' else 0 # 0 for v1, 1 for v2c. We can ignore v3 for now if simple.

    iterator = get_cmd(
        SnmpEngine(),
        CommunityData(community, mpModel=mp_model),
        UdpTransportTarget((ip, 161), timeout=1.0, retries=0),
        ContextData(),
        ObjectType(ObjectIdentity(oid_str))
    )

    errorIndication, errorStatus, errorIndex, varBinds = await iterator

    if errorIndication:
        raise TimeoutError(str(errorIndication))
    elif errorStatus:
        raise Exception('%s at %s' % (errorStatus.prettyPrint(),
                            errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
    else:
        for varBind in varBinds:
            val = varBind[1].prettyPrint()
            try:
                return float(val)
            except ValueError:
                return 0.0
    return 0.0
