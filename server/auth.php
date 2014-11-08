<?php

if (PHP_SAPI != 'cli') die();

$params = json_decode($argv[1]);
$cookies = $argv[2];

function parseCookies($line) {
    $aPairs = explode(';', $line);  
    $result = array();
    foreach ($aPairs as $pair)  
    {  
        $aTmp = array();  
        $aKeyValues = explode('=', trim($pair), 2);  
        if (count($aKeyValues) == 2)  
        {  
            switch ($aKeyValues[0])  
            {  
                case 'path':  
                case 'domain':  
                    $aTmp[trim($aKeyValues[0])] = urldecode(trim($aKeyValues[1]));  
                    break;  
                case 'expires':  
                    $aTmp[trim($aKeyValues[0])] = strtotime(urldecode(trim($aKeyValues[1])));  
                    break;  
                default:  
                    $aTmp['name'] = trim($aKeyValues[0]);  
                    $aTmp['value'] = trim($aKeyValues[1]);  
                    break;  
            }  
        }  
        $result[$aTmp['name']] = $aTmp['value']; 
    }       
    return $result;
}

if (!isset($params->authFunction)) {
    echo 'ok';
    return;
}

include $params->authInclude;
$ret = call_user_func($params->authFunction,parseCookies($cookies));

if ($ret) 
    echo 'ok'; 
else 
    echo 'failure';