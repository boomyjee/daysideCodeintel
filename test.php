<? 

function fla123($x,$y) {
    $w = array(1,2,3);
    $z = 555;
    return $x+$y+$z;
}

function bla123($x) {
    return fla123(123,234) + $x;
}

echo ini_get('xdebug.remote_enable')."<br>";
echo ini_get('xdebug.remote_host')."<br>";
echo ini_get('xdebug.remote_port')."<br>";

$y = bla123(3);

$t = new \DateTime();

$bingo = new \Bingo\Bingo;
$bingo->route

echo 123;