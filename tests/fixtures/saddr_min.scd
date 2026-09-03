<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL" version="2007" revision="B">
  <IED name="REL_A" type="SEL_451_5" configVersion="ICD-451-R408-V1-Z330010-D20250430">
    <AccessPoint name="S1">
      <Server>
        <LDevice inst="ANN">
          <LN0 lnType="LN0" lnClass="LLN0" inst="">
            <DOI name="Loc"><DAI name="stVal" sAddr="db:LOC"/></DOI>
          </LN0>
          <LN lnType="GGIO_BS32" lnClass="GGIO" inst="1" prefix="PLT1">
            <DOI name="Ind01"><DAI name="stVal" sAddr="db:PLT01"/></DOI>
            <DOI name="Ind02"><DAI name="stVal" sAddr="db:PLT02"/></DOI>
            <DOI name="Ind03"><DAI name="stVal" sAddr="db:sv06"/></DOI>
          </LN>
          <LN lnType="GGIO_BC01" lnClass="GGIO" inst="1" prefix="RB">
            <DOI name="SPCSO01">
              <SDI name="Oper"><DAI name="ctlVal" sAddr="db:RB01"/></SDI>
            </DOI>
          </LN>
          <LN lnType="CSWI_DPC" lnClass="CSWI" inst="1" prefix="BKR1">
            <DOI name="Pos"><DAI name="stVal" sAddr="db:52A|52B?0:1:2:3"/></DOI>
            <DOI name="Health"><DAI name="stVal" sAddr="db:LOC?3:1"/></DOI>
            <DOI name="Dir"><DAI name="dirGeneral" sAddr="db:BADFORM?0:1:2"/></DOI>
          </LN>
          <LN lnType="GGIO_BC01" lnClass="GGIO" inst="2" prefix="LOCK">
            <DOI name="SPCSO02">
              <SDI name="Oper"><DAI name="ctlVal" sAddr="db:LOCSTA"/></SDI>
            </DOI>
            <DOI name="Ind04"><DAI name="stVal" sAddr="db:LOCSTA"/></DOI>
          </LN>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <DataTypeTemplates>
    <LNodeType id="LN0" lnClass="LLN0">
      <DO name="Loc" type="SPS_1"/>
    </LNodeType>
    <LNodeType id="GGIO_BS32" lnClass="GGIO">
      <DO name="Ind01" type="SPS_1"/>
      <DO name="Ind02" type="SPS_1"/>
      <DO name="Ind03" type="SPS_1"/>
    </LNodeType>
    <LNodeType id="GGIO_BC01" lnClass="GGIO">
      <DO name="SPCSO01" type="SPC_1"/>
      <DO name="SPCSO02" type="SPC_1"/>
      <DO name="Ind04" type="SPS_1"/>
    </LNodeType>
    <LNodeType id="CSWI_DPC" lnClass="CSWI">
      <DO name="Pos" type="DPC_1"/>
      <DO name="Health" type="INS_1"/>
      <DO name="Dir" type="ACD_1"/>
    </LNodeType>
    <DOType id="SPS_1" cdc="SPS">
      <DA name="stVal" fc="ST" bType="BOOLEAN"/>
      <DA name="q" fc="ST" bType="Quality" sAddr="db:EN"/>
    </DOType>
    <DOType id="SPC_1" cdc="SPC">
      <DA name="stVal" fc="ST" bType="BOOLEAN"/>
      <DA name="Oper" fc="CO" type="SPCOper"/>
    </DOType>
    <DOType id="DPC_1" cdc="DPC">
      <DA name="stVal" fc="ST" bType="Dbpos"/>
      <DA name="q" fc="ST" bType="Quality"/>
    </DOType>
    <DOType id="INS_1" cdc="INS">
      <DA name="stVal" fc="ST" bType="INT32"/>
    </DOType>
    <DOType id="ACD_1" cdc="ACD">
      <DA name="dirGeneral" fc="ST" bType="Enum"/>
    </DOType>
  </DataTypeTemplates>
</SCL>
